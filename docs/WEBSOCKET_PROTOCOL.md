# WebSocket protocol

The realtime channel used by the widget, the dashboards and any custom (headless) client. It is a plain WebSocket carrying JSON text frames.
There is **no credential in the URL**: a client first obtains a single-use *ticket* over authenticated REST, opens the socket, and proves
itself with the ticket as the first frame.

Reference implementations: `packages/widget/src/main.ts` (widget), `apps/*/src/**/ticketSocket.ts` (dashboards),
`examples/headless/headless.mjs` (minimal, zero-dependency, runs in CI).

## 1. Endpoints

| Who | Ticket request (REST) | Socket URL | Ticket `kind` |
|---|---|---|---|
| customer (widget / headless) | `POST /api/v1/widget/ws-ticket/` body `{"conversation_id"}` + header `X-Widget-Session` | `/ws/v2/widget/<conversation_id>/` | `widget` |
| operator / staff, a customer conversation | `POST /api/v1/ws/ticket/` body `{"kind":"dashboard_chat","conversation_id"}` + `Authorization: Bearer <dashboard JWT>` | `/ws/v2/dashboard/<conversation_id>/` | `dashboard_chat` |
| tenant admin ↔ platform support thread | `POST /api/v1/ws/ticket/` body `{"kind":"support","conversation_id"}` | `/ws/v2/support/<conversation_id>/` | `support` |
| in-app notifications | `POST /api/v1/ws/ticket/` body `{"kind":"notifications"}` | `/ws/v2/notifications/` | `notifications` |

Production URL shape: `wss://<backend domain>/ws/v2/…` (nginx forwards `/ws/` to Daphne).
Legacy credential-in-path routes (`/ws/widget/<token>/…`, `/ws/dashboard/<jwt>/…`) exist only for development and for an explicitly
time-boxed, owner-approved migration window; they are **refused on staging/production by default**, and staff JWTs are never accepted in a URL there.
Do not build on them ([`runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md`](runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md)).

## 2. Tickets

`201 Created` → `{"ticket": "<opaque>", "expires_in": 30}`

* 256-bit random, stored server-side only as a SHA-256 hash in Redis, **TTL 30 s** (`WS_TICKET_TTL_SECONDS`).
* **Single use**: consumed atomically by the first valid auth frame. Two concurrent connection attempts: at most one wins.
* **Bound** to the kind, the subject (user or visitor session) and the *exact conversation*. Presenting it for anything else is refused and burns it.
* A ticket is issued only for a socket the server would accept right now. Anything else → uniform `404` (no oracle for conversation ids);
  an invalid/expired session → `401 {"code": "session_invalid"}`; Redis unavailable → `503 {"error": "Realtime service unavailable"}`.
* Rate limit: `WS_TICKET_THROTTLE_RATE` (default 120/min). Mint a ticket **per connection attempt**, never reuse one.

## 3. Connect and authenticate

```
client                                            server
  │  GET /ws/v2/widget/<conv>/  (Upgrade, Origin)   │
  │ ───────────────────────────────────────────────►│  accepted, but joins NO group and receives nothing yet
  │  {"type":"auth","ticket":"<ticket>"}            │
  │ ───────────────────────────────────────────────►│  ticket consumed; live authorisation re-checked
  │  {"type":"auth.ok"}                             │
  │ ◄───────────────────────────────────────────────│  now joined; frames flow both ways
```

* The **first frame must be the auth frame.** Anything else, a malformed frame, a bad/expired/replayed/mismatched ticket, or no frame within
  `WS_AUTH_TIMEOUT_SECONDS` (10 s) → the server closes with **`4401`**.
* If the ticket is valid but the caller is no longer authorised, the close code is **`4403`**.
* For browsers the handshake `Origin` is checked. For widget sockets it must match the **session's own project's** allowed domains
  (checked at connect and re-checked live). Non-browser clients without an `Origin` follow the project's domain policy
  ([`runbooks/PROJECT_ALLOWED_DOMAINS.md`](runbooks/PROJECT_ALLOWED_DOMAINS.md)).

## 4. Frames

### Client → server (after `auth.ok`)

| Frame | Meaning |
|---|---|
| `{"message": "text", "client_message_id": "<unique id>"}` | send a message (1–5000 chars after trimming). `client_message_id` makes retries idempotent: a repeated id never creates a second message |
| `{"type": "typing"}` | typing indicator for the other side |
| `{"type": "mark_read"}` | mark the other side's messages as read; the other side receives `message.seen` |

Invalid JSON and empty/over-long messages are dropped silently. Binary frames are ignored. Attachments are uploaded over REST
(`POST /widget/conversations/<id>/upload/`, operators: `POST /conversations/customer/<id>/upload/`) and arrive as ordinary `chat.message` frames.

### Server → client

| Frame | Meaning |
|---|---|
| `{"type":"auth.ok"}` | authentication succeeded |
| `{"type":"chat.message", "id", "sender_type", "content", "message_type", "metadata", "attachment_url", "client_message_id", "created_at"}` | a message (including your own, echoed — de-duplicate by `id`/`client_message_id`). `sender_type` is `VISITOR`, `USER` (staff) or system. Support threads add `sender_side` (`platform`\|`tenant`). `attachment_url` is a short-lived signed URL |
| `{"type":"typing","sender_type"}` | the other party is typing (never echoed to the typist) |
| `{"type":"message.seen","reader"}` | `VISITOR` or `USER` read the conversation |
| `{"type":"branding.updated","branding":{…}}` | the consultant's identity/presence changed (the widget refreshes its header) |
| `{"type":"rate_limited","retry_after":<seconds>}` | you sent too fast (widget 30/min, staff 60/min); the message was **not** saved — resend later |
| operator-only events | dashboard sockets also receive `conversation.queued|assigned|unassigned|transferred|escalated|priority_updated|sla_updated|sla_approaching|sla_breached|internal_note_created`; these are never sent to customer sockets |

Treat unknown `type` values as ignorable: the protocol evolves additively.

## 5. Close codes

| Code | Meaning | Client action |
|---|---|---|
| `4401` | not authenticated: bad/other first frame, bad, expired or already-used ticket, auth timeout, or legacy auth refused | fetch a **new ticket** and reconnect; after 3 consecutive `4401` stop and re-establish the session (the dashboards do) |
| `4403` | authenticated earlier but **no longer authorised**: membership removed, user disabled, session revoked/expired, tenant or project deactivated, allowed domain removed | **do not retry blindly.** Re-run the identity flow (a fresh assertion/login); if access is truly gone, show that |
| `1000`/`1001`/`1006` | normal close, going away, abnormal drop (network, worker restart, proxy) | reconnect with backoff |

## 6. Reconnect and resync — what a robust client does

1. Back off exponentially with jitter (e.g. 1 s, 2 s, 4 s … capped ~30 s).
2. **Fetch a new ticket** for every attempt (tickets are single-use and expire in 30 s).
3. Open the socket, send the auth frame, wait for `auth.ok`.
4. **Resync history over REST** after *every* (re)connect — messages sent while you were disconnected are not replayed over the socket:
   customers `GET /api/v1/widget/conversations/<id>/messages/`, staff `GET /api/v1/conversations/<id>/messages/`. Merge by message `id` /
   `client_message_id`; never duplicate.
5. Re-queue your own unsent messages with their original `client_message_id`.
6. A `401 session_invalid` from REST means the session is gone: obtain a new one (re-assert the customer; or create a guest session if allowed).

Realtime state lives in Redis and PostgreSQL, so a reconnect may land on **any** backend worker; nothing is sticky. A worker restart simply
drops its sockets with `1006`/`1001`.

## 7. Security properties

* No credential ever appears in a URL, access log or `Referer`. nginx and Daphne access logs are redacted; WebSocket URLs contain only ids.
* Unauthenticated sockets receive nothing and are closed after 10 s.
* **Live authorisation:** before every inbound frame and delivered event (result cached 5 s) and on a timer for idle sockets (30 s ± 20 %),
  the server re-checks user/session/membership/tenant/domain. Revoked → `4403`, groups left, nothing further delivered.
* Per-socket send limits and an nginx handshake limit (30/min and 20 concurrent per address).
* Visitor sockets are tied to the session *row*: token rotation keeps the socket, revocation or expiry closes it.
* Messages are scoped to one conversation group; a customer socket can never receive another conversation's frames or operator-only events.
* Redis down ⇒ no ticket can be issued or consumed (fail closed).

## 8. Minimal client (Node ≥ 22 / any browser)

```js
const t = await (await fetch(`${API}/widget/ws-ticket/`, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Widget-Session': session },
  body: JSON.stringify({ conversation_id }) })).json();
const ws = new WebSocket(`${WS}/v2/widget/${conversation_id}/`);
ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', ticket: t.ticket }));
ws.onmessage = (e) => { const f = JSON.parse(e.data);
  if (f.type === 'auth.ok') ws.send(JSON.stringify({ message: 'Hello', client_message_id: crypto.randomUUID() }));
  if (f.type === 'chat.message') render(f); };
ws.onclose = (e) => { if (e.code === 4403) handleRevoked(); else reconnectWithNewTicketAndResync(); };
```

The complete, tested version (including reconnect and history resync) is `examples/headless/headless.mjs`.
