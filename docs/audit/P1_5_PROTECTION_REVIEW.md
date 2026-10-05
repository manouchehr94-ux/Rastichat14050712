# P1-5 — complementary protection: rate limits and private attachments

Scope per the owner's request: review rate limiting of the support REST + WebSocket paths, decide whether private customer
attachments should be served through authenticated access or signed URLs, and report result and priority by real risk.

## 1. Rate limiting — what existed, what was missing, what this PR adds

| Path | Before | Now |
|---|---|---|
| `POST /auth/login/` | `login` scope 10/min per IP + nginx `rastichat_login` | unchanged |
| widget `start` / `rate` / upload / KB feedback / KB search / macro run | scoped DRF throttles | unchanged |
| **`POST /widget/init/`** (creates a Visitor + session row per call, unauthenticated) | **none** — free database bloat / session farming | `widget_init` 30/min per IP (`WIDGET_INIT_THROTTLE_RATE`) |
| **store-admin support writes** (`create`, `send_message`, `mark_read`) | none | `support_write` 60/min **per user** |
| **platform support writes** (`reply`, `assign`, `mark_read`) | none | same scope |
| **operator REST send** (`/conversations/<id>/send/`) | none | same scope |
| ticket mint / session rotate / revoke | n/a (new) | `ws_ticket` 120/min, `widget_session` 30/min |
| widget WebSocket messages | Redis fixed window per session (30/min) | unchanged |
| **operator and support WebSocket messages** | **none** (the REST throttle never reaches `receive_json`) | Redis fixed window **per authenticated user** across all their sockets (`STAFF_WS_MESSAGE_RATE_LIMIT`=60 / `..._WINDOW_SECONDS`=60); the client receives `{"type":"rate_limited","retry_after":N}` instead of a silent drop; `typing` / `mark_read` are not counted |
| WebSocket connection rate | nginx `limit_req` 30/min + `limit_conn` 20 per IP | unchanged |

Reads (inbox lists, message history) are deliberately **not** throttled — they are polled and cheap; the throttles protect the
write paths that create rows, broadcast to other people or trigger automations.

Remaining, lower priority (not changed): other staff write endpoints (internal notes, tags, macros, KB admin, team/queue admin) have
no per-user throttle (authenticated + role-gated, so abuse needs a compromised account; P2); WebSocket `typing`/`mark_read` events
are not limited (fan out only inside one conversation; P2). Throttle counters live in the Django cache / Redis; with several
backend replicas, point `CACHES` at Redis for exact per-user DRF counts (the WS limiter already uses Redis).

## 2. Private attachments (`/media/…`) — assessment

**Facts (code + deploy config):**
* Uploads are validated (magic-number sniffing, size caps, optional scan hook) and stored under `attachments/YYYY/MM/DD/` with a
  random `uuid4().hex` name — `conversations/media_validation.py`.
* nginx serves `/media/` straight from disk, **no authentication**, `Cache-Control: private, max-age=3600`, no directory listing
  (`deploy/nginx/sites/backend.conf.template`). `attachment_url` is returned only inside authorised API/WS payloads.
* The same model applies to knowledge-base attachments (`docs/runbooks/STAGING_DEPLOYMENT.md` "Known limitations").

**Threat model.** It is a *capability URL*: 128 bits of unguessable randomness, so enumeration/guessing is not realistic. The real
exposures are leakage of the URL itself, which then works **forever and for anyone**: reverse-proxy/CDN access logs, browser history
and sync, copy/paste or forwarding of a link, screenshots of the dashboard, Referer to third-party pages, and — important for
customer chats — a customer's image (an ID card, a receipt, an address label) staying fetchable after the conversation is closed,
the session revoked, the staff member removed, or the store deactivated. Revocation (P1-1/P1-2) therefore stops *new* link
discovery but cannot kill a link already leaked.

**What is wrong with "unguessable file name = access control" (re-assessed; this replaces the earlier "P2" conclusion).**
An unguessable name defeats *enumeration*, nothing else. It is a bearer secret that, once seen, works forever and for anyone, and
this system *writes the secret down itself*: every `GET /media/attachments/<uuid>.jpg` is recorded with its full path in the nginx
access log, in browser history, in any reverse proxy / CDN / uptime tool in front of it, and in whatever the dashboards or the
customer's browser sync. Anyone who ever sees a log line — an ops engineer, a log shipper, a backup of `/var/log/nginx` — holds a
permanent key to that customer's file, with no relation to who they are, which store they work for, whether the conversation is
closed, the customer's session revoked, the staff member removed, or the workspace deactivated. P1-1/P1-2 revocation closes
*sockets and APIs*; it cannot close a link that is already out. For private customer content (IDs, receipts, addresses, voice
notes, payment screenshots) that is a real, non-theoretical exposure, and it also breaks the tenant-isolation promise made for
chats and the store↔platform support channel.

**Decision (revised): yes — chat attachments must be served only after an authorization check or through short-lived signed URLs.
Priority: P1, a hard gate before the operational RastiSi connection** (not a pilot-with-internal-staff blocker, because the
interim mitigations below remove the largest leak path, but it must land before real store customers' content flows). It is
proposed as an **independent security PR (P1-6)**; it is not part of this PR because it changes all three clients and needs the
staging nginx to be validated.

**Interim mitigation (done in the P1-3 branch, already stacked below this PR; needs only an nginx reload):**
* the nginx access log (`rastichat_redacted` format) no longer records `/media/(kb_)attachments/…` file names — the log can no
  longer be turned into a list of working links (proven by `scripts/nginx/test-log-redaction.sh`);
* `/media/` responses carry `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`.
Logs written *before* this change still contain file names: rotate/delete them per the retention policy, and treat sensitive
attachments referenced there as exposed.

**Proposed P1-6 design (independent PR; needs the staging nginx to validate):**
1. `attachment_url` becomes `/api/v1/attachments/<message_id>/?sig=<signed token>` where the token is a `django.core.signing`
   payload `{message, audience}` with `max_age` ≈ 10 min, minted per response for the *requesting* identity (operator JWT / visitor
   session / ticket) — `<img>` and `<audio>` cannot send headers, so a signed query token is the only practical carrier; unlike a
   credential it is short-lived and bound to one message.
2. The view re-checks authorization at fetch time (same `ws_access` rules: membership, session validity, active workspace) and replies
   `200` with `X-Accel-Redirect: /protected-media/<path>` (nginx `internal;` location aliasing the media dir), `Cache-Control: private,
   no-store`, `Content-Disposition: inline`, `X-Content-Type-Options: nosniff`.
3. Remove the public `location /media/` from nginx **after** clients have refreshed (old links in already-rendered history stop
   working; the history endpoints re-issue fresh signed URLs on every fetch).
   *Audience binding:* the same message is broadcast to the visitor and to staff in one channel-layer group, so the token cannot
   be bound to one recipient there; it is bound to `{message, conversation}` with a short `max_age`, and **history/list responses
   re-sign on every fetch** so a late render just refetches. Clients get one retry-on-error (`onerror` → refetch the message URL).
4. Migration for existing messages: none needed in the database (the URL is computed from `attachment` on read). KB public
   attachments can stay public; only `INTERNAL`-visibility and chat attachments move.
5. Test plan: unauthorized/expired/tampered/other-conversation tokens → 403/404; revoked session → 403; nginx direct `/media/` → 404
   on staging; large-file range requests; voice playback in Safari/Chrome (range support is required for `<audio>`).
Risks: every attachment fetch hits Django (mitigated by `X-Accel-Redirect`: Django only authorizes, nginx streams); clients that cached
absolute `/media/` URLs break once the public location is removed (hence step 3 ordering).

## 3. Priority summary
| item | priority |
|---|---|
| `widget_init` throttle, staff REST/WS write limits | done in this PR (P1) |
| authenticated/signed attachments | **P1 gate before the operational RastiSi connection** — separate PR P1-6, designed above; interim log/header mitigation already in the P1-3 branch |
| per-user throttle for remaining staff write endpoints; `typing`/`mark_read` limits | P2 |
| shared Redis cache for exact multi-replica DRF counters | P2 (operational) |
