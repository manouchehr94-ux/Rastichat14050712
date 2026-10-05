# Private chat attachments (P1-6)

## Problem
`attachment_url` was `/media/attachments/<uuid>.<ext>`: an unguessable name that nginx served to **anyone, for ever**. A random name
defeats guessing, nothing else — it is a bearer secret, and this system wrote it down itself: every fetch was recorded with its full
path in the nginx access log, in browser history, in any proxy in front. Whoever ever saw a log line held a permanent key to a customer's
file (ID card, receipt, address, voice note), unrelated to who they are, which store they work for, whether the conversation was closed,
the customer's session revoked, the staff member removed or the store deactivated. Measured on the isolated staging stack before this
change: the link kept answering `200` after the visitor session was revoked, the project deactivated and the workspace deactivated.

## Design
```
history / upload response ─► attachment_url = /api/v1/attachments/<message>/?sig=<token>      (per requester)
room broadcast over WS    ─► same, audience = "this conversation's visitor"                     (not bound to one recipient)
dashboards / widget       ─► GET /attachments/<message>/refresh/  (JWT or X-Widget-Session)     a fresh URL when one expired
browser <img>/<audio>     ─► GET /api/v1/attachments/<message>/?sig=…  ─► nginx ─► Django authorizes ─► X-Accel-Redirect
                                                                                  └─ nginx streams /protected-media/<file> (internal)
```
* **Token** (`conversations/attachment_access.py`): `django.core.signing` with its own salt, expires after `ATTACHMENT_URL_TTL_SECONDS`
  (600). Names exactly one message and one audience: `u` a staff user, `v` one visitor session, `c` the conversation's own visitor.
* **No permission in the token.** Every fetch re-checks that identity's *current* access with the same rules the WebSockets use
  (`ws_access`): user active + exact membership (workspace admin/owner or platform staff for support conversations), visitor session live
  and owning the conversation, project / workspace / platform active. Removing a member, deactivating a user, revoking or expiring a
  session or deactivating a store closes access to **new** fetches immediately — not after the URL expires.
* Every refusal (no/garbled/tampered/expired signature, other message, other store, revoked identity…) is the same `404`.
* **nginx streams, Django authorizes** (`deploy/nginx/snippets/media-locations.conf.template`): `/protected-media/` is `internal` (a direct
  request is a 404 from nginx itself), so Range requests — voice notes in Safari/Chrome, large files — work and no file passes through
  Python. `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`.
* **KB attachments that are meant to be public stay public** (`/media/kb_attachments/`). INTERNAL-visibility KB files are unchanged
  (known limitation, see STAGING_DEPLOYMENT.md).
* **No data migration.** The URL is computed from the stored file on every read; files stay where they are, so URLs already embedded in
  history responses simply become signed on the next fetch. Backups are unaffected.

## Configuration
| variable | default | meaning |
|---|---|---|
| `ATTACHMENT_URL_TTL_SECONDS` | `600` | lifetime of a signed URL |
| `ATTACHMENT_SERVE_MODE` | `accel` on staging/production, `django` otherwise | `accel` = `X-Accel-Redirect` (needs the nginx location above); `django` = Django streams the file (development, no Range) |
| `ATTACHMENT_ACCEL_PREFIX` | `/protected-media/` | the internal nginx location |
| `ATTACHMENT_DOWNLOAD_THROTTLE_RATE` / `ATTACHMENT_URL_THROTTLE_RATE` | `600/min` / `300/min` | per address / per user |
| `CHAT_ATTACHMENTS_PUBLIC` (install-time, `scripts/nginx/install-sites.sh`) | `0` | `1` = TEMPORARY compatibility: re-opens `/media/attachments/` by file name exactly as before |

## Rollout (nothing here is run by the PR)
1. **Deploy backend + dashboards + widget together**, and install nginx with `CHAT_ATTACHMENTS_PUBLIC=1`. From this moment every API/WS
   response carries signed URLs; pages that were already open, or old cached widget bundles, may still hold `/media/attachments/` URLs
   and they keep working. Old widgets need no update: they render `attachment_url` as an opaque string.
2. **Wait for old URLs to drain**: the old `Cache-Control: private, max-age=3600` (1 h) plus however long you allow cached widget bundles.
3. **Close it**: re-run `scripts/nginx/install-sites.sh` without the flag (the default) → `/media/attachments/` answers 404.
   Verify with `scripts/nginx/test-media-routing.sh` and one real attachment: signed URL → 200, old `/media/…` URL → 404, direct
   `/protected-media/…` → 404.
4. **Log hygiene**: access logs written before this release (and before `rastichat-log-redaction.conf`) contain attachment file names.
   They are now useless as keys (step 3) but still hold customer-linked paths: rotate/delete per your retention policy.
   The application server's own access log is covered too: the container runs `python -m config.daphne_server` (a thin Daphne wrapper), which logs method, path, status and size but replaces any query string with `?[redacted]` and masks credential path segments, so `sig=` / `session_token=` never reach container logs. Do not start Daphne with the bare `daphne` command in staging/production.

Rollback: re-run `install-sites.sh` with `CHAT_ATTACHMENTS_PUBLIC=1` (instant, nginx reload) and/or deploy the previous backend — the old code
issues `/media/…` URLs and nginx serves them in compatibility mode. No database step either way.

## What a user sees
Images and voice notes load as before. A tab left open past the TTL: when an `<img>`/`<audio>` holding an expired URL fails, the
dashboards and the widget ask `…/refresh/` for a new one and retry **once** (no loops on a genuinely missing file). Clicking an *old*
image to open it full-size in a new tab after the TTL gives a 404 until the conversation is reopened (the dashboard re-fetches history
with fresh URLs) — the one UX cost of short-lived links, and the reason the TTL is configurable.

## Tests
* `conversations/tests_attachment_access.py` (37, negative-first): tampered / expired / foreign-salt / other-message tokens, other store,
  inactive user, membership removed, workspace/project deactivated, session revoked/expired, other visitor with a crafted token,
  support-conversation roles, only GET/HEAD, headers, content types, Django mode, audience binding (own response vs room broadcast),
  refresh endpoint for staff and visitors, no query-string credential.
* `scripts/nginx/test-media-routing.sh` (real nginx, also a CI job): internal location 404, chat attachments 404 by default and
  public only in compatibility mode, KB public, authorized fetch streamed, `206` + `Content-Range` for Range requests, headers.
* Client tests: widget (`main.test.ts`), both dashboards (`attachmentRefresh.test.ts`).
* Isolated staging run with real nginx/TLS: see `docs/runbooks/DJANGO52_STAGING_TEST_PLAN.md`.
