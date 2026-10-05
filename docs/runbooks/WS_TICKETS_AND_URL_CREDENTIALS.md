# WebSocket tickets and credentials-in-URL (P1-3)

## Problem
A 60-minute JWT (dashboards) and the visitor `session_token` (widget) travelled in the WebSocket URL *path*
(`/ws/dashboard/<JWT>/…`, `/ws/widget/<token>/…`) and the widget sent `?session_token=` on REST GETs. URLs are written to
reverse-proxy access logs (`deploy/nginx` `access_log`), browser history, tooling consoles (the operator dashboard even
`console.log`-ed the full socket URL). Anyone with log access held a live credential.

## Design
```
operator / customer ──POST /api/v1/ws/ticket/        (JWT in Authorization header)
                    ──POST /api/v1/widget/ws-ticket/ (session in X-Widget-Session header)
        ◄── { ticket, expires_in: 30 }           random 256-bit, stored as SHA-256 hash in Redis, TTL 30 s
        ──► wss://host/ws/v2/<kind>/<conversation>/   (no credential in the URL)
        ──► first frame {"type":"auth","ticket":"…"}  consumed atomically (MULTI GET+DEL) → single use
        ◄── {"type":"auth.ok"}                        only now are channel-layer groups joined
```
* A ticket is bound to the **kind** (dashboard chat / support / notifications / widget), the **subject** (user id or visitor-session
  id) and the **exact conversation**. Replaying it elsewhere is rejected *and burns it*.
* Tickets are only minted for sockets the consumer would accept (`conversations/ws_access.py` is shared), with a uniform 404
  otherwise, and re-checked at connect and live (P1-1 revalidation).
* An unauthenticated socket joins **no group**; it is closed with 4401 on a bad/other first frame or after
  `WS_AUTH_TIMEOUT_SECONDS` (10 s). 4403 = authenticated once, no longer authorized.
* Widget sockets are tied to the session *row*: token rotation keeps the socket, revocation/expiry closes it.
* Redis down ⇒ no ticket can be minted or consumed (fails closed; the channel layer needs Redis anyway).
* The session credential for widget REST moves to the `X-Widget-Session` header (the JSON body is still accepted for POSTs;
  `X-Widget-Session` is in `CORS_ALLOW_HEADERS`).

## The legacy mechanism and the one permitted exception
Legacy = token-in-path WebSocket routes (`/ws/widget/<token>/…`, `/ws/dashboard/<jwt>/…`, …) and `?session_token=` on REST.

| environment | state |
|---|---|
| `development` / tests | **on** by default (local tooling keeps working) |
| `staging` / `production`, nothing configured | **off** — and the deploy gate (`check --deploy --fail-level WARNING --tag security`) is green |
| `staging` / `production`, owner-approved exception | **widget only**, until a fixed date — see below |

**Staff JWTs are never accepted in a URL on staging/production, under any setting.** Dashboards are first-party and ship
with the backend; only already-published *widget bundles* (cached, or pinned on customer sites at
`/widget/<version>/widget.js`, which is served `immutable`) cannot be updated by us.

### The exception (needs a separate, explicit approval from the owner — do not set it otherwise)
| Item | Value |
|---|---|
| Scope | visitor session tokens of the **widget** only (WebSocket path + `?session_token=` on widget REST) |
| Duration | a fixed UTC end date, **at most 21 days** from the day it is set (a constant in `settings.py`, not an env var) |
| Variables (all four, validated at startup — any mistake refuses to start) | `LEGACY_URL_CREDENTIALS_ENABLED=1`, `LEGACY_URL_CREDENTIALS_ACK=accept-credentials-in-urls`, `LEGACY_URL_CREDENTIALS_SCOPE=widget`, `LEGACY_URL_CREDENTIALS_UNTIL=YYYY-MM-DD` |
| Self-expiry | enforced **on every connection/request** (`common/legacy_credentials.py`): at the end date a running process stops accepting legacy credentials without a restart or a deploy |
| Gate | while the window is open `check --deploy` prints `common.I002 TEMPORARY EXCEPTION ACTIVE … until <date>` (Info: it is visible in every CI/deploy log and does not fail the gate). **After** the date, or if a restart finds the variables past their date, legacy stays off and the gate fails with `common.W003` until the variables are removed — it cannot linger silently. |
| Compensating controls | (1) nginx never logs a credential: `deploy/nginx/conf.d/rastichat-log-redaction.conf` (`rastichat_redacted` log format: credential path segment → `[redacted]`, no query string), proven by `scripts/nginx/test-log-redaction.sh` and a CI job; (2) a visitor session token is revocable, expires (30 d sliding, 180 d hard) and can only reach that visitor's own conversations; (3) project allowed-domain / Origin checks (P1-4) still apply; (4) usage is counted per project |
| Monitoring | `manage.py report_legacy_credential_usage --days 14` (read-only) lists legacy uses per day/surface/project. Review it every few days; contact the projects still listed; **shorten** the date if usage reaches zero, never extend past the cap without a new approval |
| Rollback | not needed for security (remove the variables = instant off). If the end date arrives while a project still uses an old bundle, that widget's *live socket and history reads* stop (see "What an old widget experiences"); fix = that customer updates the snippet. A new, separately approved window may be opened, again ≤ 21 days |

### Release plan (every step is tested; none is run by this PR)
Before step 1 the owner decides whether any exception is needed at all. Check what is out there first: after step 0,
`report_legacy_credential_usage` against *staging/production traffic* tells which projects use an old bundle.

0. **Log hygiene, no application change.** Install `rastichat-log-redaction.conf` and the updated `backend.conf.template`
   via `scripts/nginx/install-sites.sh` (it runs `nginx -t` first and never reloads on failure). From now on no credential is
   written to the access log. Logs written earlier may contain credentials: rotate/delete them per the retention policy
   and treat any still-live token found there as exposed (visitor sessions can be revoked: `purge_expired_visitor_sessions`
   / session revoke endpoints).
1. **Deploy backend + both dashboards + the new widget bundle in one maintenance step** (dashboards need the backend's
   `ws/ticket/`). With the exception **not set**, the gate is green. Operators with an old dashboard tab see the socket
   close once and must reload (a visible banner, no data loss — the first-party dashboards cannot be left on JWT-in-URL).
   New widget bundle: `/widget.js` is `no-cache, must-revalidate`, so every embedding site that uses the unversioned URL
   gets it on its next page load.
2. **Only if step 1 left customers on old bundles** (pinned `/widget/<version>/…` or tabs open across the deploy) and the
   owner approves: set the four variables with the shortest date that covers them (≤ 21 days). Restart. The gate prints
   `common.I002`. Old widgets keep working; staff JWTs in URLs stay refused.
3. Watch `report_legacy_credential_usage`. Contact the projects listed.
4. At the end date legacy turns itself off. Remove the four variables in the next deploy (the gate is red with `common.W003`
   until you do) — this is the reminder, not an incident.

### What an old widget experiences when its window ends
Its WebSocket refuses (4401) and history GETs with `?session_token=` return 401; messages it *sends* by POST with the body
credential still work. Conversations, messages and the visitor's session are untouched. The customer's next page load with a
current bundle resumes the same conversation. Nothing is deleted, and nothing is cut off *before* the date.

## Tests
`conversations/tests_ws_tickets.py` (issuing, hashing/TTL, single use incl. a 32-thread race, kind/conversation binding, no group
access before auth, timeout/bad frames, replay, revocation between issue and use, live revocation, token rotation vs revocation,
legacy switch), `conversations/tests_legacy_rollout.py` (phase 1 window open / phase 2 window over, per-project usage,
staff JWTs refused, ticket clients unaffected, history intact), `config/tests_settings_guardrails.py` (every exception variable
validated; Info/Warning gate behaviour), `scripts/nginx/test-log-redaction.sh` (real nginx), widget `main.test.ts`, dashboard
`ticketSocket.test.ts` / `api.test.ts`.
