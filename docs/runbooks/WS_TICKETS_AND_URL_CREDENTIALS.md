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

## The legacy mechanism
`LEGACY_URL_CREDENTIALS_ENABLED` (token-in-path WebSocket routes and `?session_token=`):
| environment | default | enabling it |
|---|---|---|
| `development` / tests | **on** (local tooling keeps working) | — |
| `staging` / `production` | **off** | startup refused unless `LEGACY_URL_CREDENTIALS_ACK=accept-credentials-in-urls`; with the ack, `check --deploy --tag security` reports `common.W002` (CI/deploy gate goes red) |

## Rollout / migration plan (do NOT run against production without the owner's approval)
Old clients (cached widget bundles on customer sites, an old dashboard tab) keep using the legacy routes, so ship in this order:
1. Deploy the new **backend** with `LEGACY_URL_CREDENTIALS_ENABLED=1` + `LEGACY_URL_CREDENTIALS_ACK=accept-credentials-in-urls`
   (the gate is red on purpose — it is the reminder that this is temporary). New and old clients both work.
2. Deploy the new **dashboards** and **widget bundle**. Widget bundles are cached by embedding sites: keep step 3 until the
   old bundle's traffic is gone. Measure it: legacy sockets are visible in the access log as `/ws/widget/<uuid>/` (v2 is
   `/ws/v2/widget/`), legacy REST as `?session_token=`.
3. Remove both variables → legacy routes close with 4401, query-string tokens are ignored, the gate is green again.
Rollback at any step: re-set the two variables (instant, no redeploy of clients).

Compatibility notes: a customer with an old widget tab open loses its live socket at step 3 and reconnects with the new bundle on
the next page load; REST/session/conversation data are untouched. A user with an old dashboard tab sees the socket close and must
reload. Both are visible failures, not silent ones.

## Tests
`conversations/tests_ws_tickets.py` (issuing, hashing/TTL, single use incl. a 32-thread race, kind/conversation binding, no group
access before auth, timeout/bad frames, replay, revocation between issue and use, live revocation, token rotation vs revocation,
legacy switch), `config/tests_settings_guardrails.py` (`test_legacy_url_credentials_*`), widget `main.test.ts`, dashboard
`ticketSocket.test.ts` / `api.test.ts`.
