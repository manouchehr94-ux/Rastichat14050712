# Headless integration (no widget)

You do not have to use the RastiChat widget. A host that wants its own chat UI uses **the same public protocol the widget
uses**: Integration Contract v1 on your server, and the versioned REST + WebSocket API from the browser/app. There is no
private channel the widget has and you do not.

`examples/headless/headless.mjs` is a complete, runnable, zero-dependency (Node ≥ 22) walk through every step below. It runs in
`e2e/reference-host/run.sh` against a real stack on every CI run, so this document cannot drift from the behaviour.

## Why there is no SDK package (decision)
The master specification asks for a headless path and says an SDK is optional ("if useful… do not create an unnecessary giant
SDK"). The whole client surface is nine HTTP calls and one WebSocket handshake, all versioned (`/api/v1/`, schema at
`/api/schema/`). A thin wrapper would add a second thing to version without removing any step a host must still do on its
own server (sign assertions). If several hosts later ask for one, it is additive — see `V1_SCOPE_AND_DEFERRALS.md`.

## The flow

| # | Who | Call | Notes |
|---|---|---|---|
| 1 | host server | `PUT /api/v1/integrations/tenants/{id}/` | idempotent; returns `project_public_key`. Put your site's host in `verified_domains` |
| 2 | host server | `PUT …/members/{user}/` | staff who will answer (optional) |
| 3 | host server | `PUT …/contexts/{customer}/` | optional, small flat context shown to operators (scope `context:write`) |
| 4 | browser/app | `GET /api/v1/widget/config/?project_key=…` | launcher, pre-chat form, identity policy: render your UI from it |
| 5 | host server → app | sign a customer assertion for the *already-logged-in* user | see Contract §7. Never derive claims from request parameters |
| 6 | browser/app | `POST /api/v1/identity/customer/ {project_key, assertion}` | → `session_token` (verified visitor). Guest: `POST /widget/init/ {project_key}` |
| 7 | browser/app | `POST /api/v1/widget/start/` with header `X-Widget-Session` | → `{id}` = conversation; body may carry `pre_chat` answers |
| 8 | browser/app | `POST /api/v1/widget/ws-ticket/ {conversation_id}` (+ session header) | single-use ticket, ~30 s |
| 9 | browser/app | open `wss://…/ws/v2/widget/{conversation_id}/`, first frame `{"type":"auth","ticket":…}` | wait for `{"type":"auth.ok"}`; then frames `{"message", "client_message_id"}` out and message objects in |
| 10 | browser/app | `GET /api/v1/widget/conversations/{id}/messages/` | **history resync**: call it after every (re)connect; de-duplicate by message `id` / `client_message_id` |

### Rules a custom UI must follow (the widget does all of these)
* **Credentials never go in a URL.** Session token → header `X-Widget-Session`; WebSocket auth → first frame ticket.
* **Reconnect = new ticket.** Tickets are single-use; on close, back off, fetch a new ticket, reconnect, then resync history.
* **Session expiry:** a `401` from any widget call means the session is gone — re-run step 5/6 (or create a guest session).
* **Origin:** browsers send `Origin`; it must match the tenant's `verified_domains` (RastiChat enforces it; it is a defence against other
  websites, not authentication). Server-side clients without an `Origin` follow the project's domain policy.
* **Identity is asserted by your server only.** A customer id typed or stored in the browser is never accepted as proof.
* **Idempotent sends:** always send a unique `client_message_id`; retrying the same id never duplicates a message.
* **Attachments:** URLs in messages are short-lived signed URLs; refresh with `GET /widget/attachments/{id}/refresh/`.
* **Pre-chat:** if `config.pre_chat.enabled`, collect the declared fields and send them as `pre_chat` in step 7; the server validates
  them and answers `400 {errors:{field:message}}` per field.

### Operator side
Operators/staff use the regular RastiChat dashboard (SSO, Contract §7) — or the dashboard REST API directly after
`POST /api/v1/identity/staff/` (the script shows a reply via `POST /api/v1/conversations/{id}/send/`).

## Errors
Contract errors use the v1 envelope `{"error": {"code", "message", "request_id"}}` (Contract §4); widget endpoints keep their
existing `{error, code}` shape (unchanged for backward compatibility).

## Running the proof yourself
```bash
RASTICHAT_URL=http://localhost:8080 KEY_ID=ick_… INTEGRATION_SLUG=<slug> PRIVATE_KEY_FILE=host.private.pem \
  node examples/headless/headless.mjs        # needs scopes tenants:*, identity:customer, identity:staff, context:write
```
