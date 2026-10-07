# Troubleshooting

Symptom → cause → fix. For each case the *client-visible* signal is given first; the precise reason is almost always in the backend log
(`integration_token_refused event=<reason> …`, `rastichat_event …`) or in the monitoring counters — RastiChat deliberately returns the
same generic answer for several different failures so that clients cannot use it as an oracle.
Quote the `request_id` of an error envelope (or the `X-Request-ID` response header) when asking for help.

## Widget

### The chat icon does not appear
1. **Script/URL.** Does `…/widget.js` load (no CSP/mixed-content block)? Host-page CSP needs `script-src` and `connect-src` for the RastiChat origin
   (and `wss:`) — see [`widget/EMBEDDING.md`](widget/EMBEDDING.md).
2. **`projectKey`** must be the tenant's `project_public_key` (returned by provisioning; shown in the dashboard under *Widget settings → Embed*).
3. **Allowed domains.** In the browser console / network tab, `POST …/widget/init/` or `GET …/widget/config/` answering `403 origin_not_allowed`
   (or a uniform `404` on config) means the page's origin is not in the project's allowed domains → add the exact host to the tenant's `verified_domains`.
4. **Configuration hides it:** `launcher.enabled=false`, or the current path matches `visibility.hide_on_paths` / is not in `show_on_paths`.
   After a client-side navigation call `RastiChat.refreshVisibility()`.
5. **Signed-in-only project** (`identity.authenticated_only`) and `bootstrap` returned `null`: the launcher shows greyed with a "sign in" message by design.
6. The tenant/project is suspended or archived (provisioning `status`): the widget stays hidden/offline.

### `403 origin_not_allowed` / `origin_required` / `no_domains_configured`
* `origin_not_allowed`: the `Origin` of the page is not in the project's allowed domains (exact host; `host:port` for non-default ports;
  `*.example.com` matches sub-domains but **not** `example.com` itself). Fix via `verified_domains` (host) or the project settings.
* `origin_required`: a session was requested without an `Origin` header (server-side/native client). Session creation needs a verifiable Origin;
  use the signed-assertion path from your server instead.
* `no_domains_configured`: the deployment sets `WIDGET_REQUIRE_ALLOWED_DOMAINS=1` and this project has no domains.
* `Origin: null` (sandboxed iframes, `file://`) never matches.

### Browser blocks the request: CORS error
* **Dashboards:** the dashboard's origin must be in `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` (full scheme + host, no trailing slash).
* **Widget endpoints** (`/api/v1/widget/…`, `/api/v1/identity/customer/`, `/api/v1/kb/public/`) accept origins listed in any *active* project's
  allowed domains (cached ≈ 60 s), so no env change is needed — a CORS error there is an allowed-domains problem (above), or the
  `Access-Control-Allow-Origin` header is stripped by an intermediary.
* The staff-facing and integration endpoints are intentionally **not** opened to arbitrary origins.
* A CORS failure from `fetch('/chat/identity')` on your *own* backend is your backend's CORS/cookie configuration, not RastiChat's.

## Identity and integration authentication

Integration error envelope: `{"error": {"code", "message", "request_id"}}`. Common codes:

| Code (HTTP) | What it means | Fix |
|---|---|---|
| `missing_token` (401) | no `Authorization: Bearer …` | send it |
| `invalid_token` (401) | **deliberately generic**: malformed, unknown `kid`, bad signature, wrong `aud`, wrong `iss`, bad claims, `iat` in the future, TTL too long | look at the server log line `integration_token_refused event=…` for the real reason: `unknown_kid`, `wrong_audience`, `wrong_issuer`, `bad_signature_or_claims`, `ttl_too_long`, `iat_in_future` |
| `token_expired` (401) | `exp` has passed (5 s leeway) | mint a fresh token per request; check the host's clock (NTP) |
| `token_replayed` (401) | the same `jti` was already used | **a token is single use** — mint a new one for every request *and every retry* (`jti` must be unique, 8–128 chars) |
| `binding_mismatch` (401) | the API token's `htm`/`htu`/`bh` do not match the actual request | sign the exact method, **path as sent** (no scheme/host/query) and SHA-256 of the raw body bytes |
| `key_revoked` (401) | the key was revoked or is outside its validity window | use the current key; ask the operator to add one |
| `integration_disabled` (403) | the integration is switched off | operator: `integration_set_active --slug … --enable` |
| `scope_denied` (403) | key ∩ integration scopes do not include what this endpoint needs | ask the operator for the scope (least privilege — only what you need) |
| `tenant_not_found` (404) | unknown tenant — **also returned for another integration's tenant** | check the external tenant id and that you provisioned it with this integration |
| `tenant_unavailable` / `identity_disabled` (403) | tenant suspended/archived, or the person/customer is disabled | restore with `PUT … {"status":"active"}` / `…/enable/` |
| `tenant_mismatch` (403) | the assertion names a tenant that does not own this `project_key` | use the project key of the *same* tenant the assertion names |
| `origin_mismatch` (403) | assertion carries an `origin` claim that differs from the request `Origin` | sign the real page origin (or omit `origin`) |
| `invalid_assertion` (400) | wrong `actor` for the endpoint (customer assertion sent to `/identity/staff/` or vice versa), or malformed claims | use the right endpoint/actor |
| `rate_limited` (429) | per-integration or failed-auth throttle | back off using `Retry-After` |
| `503 replay_store_unavailable` | Redis is unreachable; integrations fail closed | operator restores Redis; retry later |

**Assertion rejected, step by step:** (1) is the JWT header `{"alg":"EdDSA","kid":"<kid>"}`? (2) is `aud` `<audience>:identity` for assertions
(`<audience>:api` for server calls) and does the audience equal the deployment's `INTEGRATION_TOKEN_AUDIENCE`? (3) `iss` = your integration slug;
(4) `exp − iat ≤ 120 s` (assertions) / `≤ 60 s` (API); (5) `actor`, `tenant` (customers and tenant staff), `role` (`owner|admin|operator`) present;
(6) the assertion was not already used — each browser page load needs a **fresh** assertion from your endpoint; do not cache it.
Use the reference implementation `examples/reference-host/lib/rastichat.mjs` to compare your signer byte for byte.

### Staff SSO lands on a login screen or "session expired"
The assertion belongs in the dashboard URL **fragment**: `<operator dashboard>/admin/sso#assertion=<jwt>&next=/` (note the `/admin` base path;
the platform dashboard uses `/platform/sso`). A query string is not accepted. Assertions live ≤ 2 minutes and are single-use, so sign a fresh one
when redirecting. Dashboard sessions from SSO last `INTEGRATION_STAFF_SESSION_MINUTES` (30) and have no refresh token — redirect through SSO again.

### Customer is treated as a guest although signed in
`bootstrap` returned `null`/threw, or the assertion was refused (see above — check the browser network tab for `POST …/identity/customer/`).
Guests keep working only if the project allows them; on signed-in-only projects the customer sees the "sign in" state.

## Realtime (WebSocket)

| Symptom | Cause / fix |
|---|---|
| closes immediately with **4401** | the first frame was not `{"type":"auth","ticket":…}`, or the ticket is expired (30 s), already used, issued for another conversation/kind, or no frame arrived within 10 s. Fetch a **new ticket for every connection attempt**, and send the auth frame as soon as the socket opens |
| closes with **4403** | the caller is no longer authorised (membership removed, user/customer disabled, session revoked/expired, tenant/project deactivated, allowed domain removed). Re-establish identity; do not loop on reconnects |
| ticket request returns `404` | uniform "not yours or not found": wrong conversation id, other tenant, or the session/user lacks access |
| ticket request returns `401 session_invalid` | visitor session expired/revoked → re-run the identity exchange or create a guest session |
| ticket request returns `503` | Redis/realtime unavailable → retry with backoff |
| handshake fails, no close code | nginx is not forwarding the upgrade (`Upgrade`/`Connection` headers; `conf.d/rastichat-websocket-map.conf` not included) or a proxy in front strips WebSockets; `limit_req`/`limit_conn` (30 handshakes/min, 20 concurrent per address) tripped for a shared address |
| messages missing after a drop | normal: **resync history over REST after every reconnect** and de-duplicate by `id`/`client_message_id` ([`WEBSOCKET_PROTOCOL.md`](WEBSOCKET_PROTOCOL.md) §6) |
| works locally, not through the proxy | `wss://` must terminate at nginx → `/ws/` location; Origin must be an allowed domain for widget sockets |
| `{"type":"rate_limited"}` frames | too many messages (widget 30/min, staff 60/min); the message was not saved — resend after `retry_after` |

## Attachments

* **`404` on an attachment URL**: signed URLs live 10 minutes and are re-authorised on every fetch. Expired, tampered, other-message and
  *revoked-identity* requests are all the same `404`. Refresh with `GET /attachments/<message>/refresh/` (staff) or
  `GET /widget/attachments/<message>/refresh/` (customer); the widget and dashboards do this automatically once. History responses always carry fresh URLs.
* `404` on `/media/attachments/…`: expected — chat attachments are not public files any more.
* `404` from `/protected-media/…` when requested directly: expected (`internal` location).
* Every fetch 404s while the file exists: nginx `/protected-media/` alias (`MEDIA_HOST_PATH`) does not point at the backend's `MEDIA_ROOT`, or
  `ATTACHMENT_SERVE_MODE=accel` without the nginx location installed ([`runbooks/PRIVATE_ATTACHMENTS.md`](runbooks/PRIVATE_ATTACHMENTS.md)).
* Audio/video cannot seek: the response is not coming from nginx (Range requires `accel` mode).

## Provisioning and mapping

* **Tenant mapping mismatch** (`tenant_mismatch`, `tenant_not_found`): the *external tenant id* is the mapping key and is immutable; a project key
  belongs to exactly one mapping of one integration. Re-`PUT` the tenant to retrieve `project_public_key`/`workspace_id`; never guess ids.
* `409 tenant_archived`: `DELETE` archived it; restore with `PUT … {"status":"active"}`.
* `400 validation_error` with `details`: unknown fields are refused (typos), `external_tenant_id` must match `[A-Za-z0-9._:~-]` and start alphanumeric.
* Seed-only fields (`defaults.*`) are applied once; change them later from the dashboard, not by repeating provisioning.
* A tenant's staff cannot see the chat after SSO: the assertion's `role` must be `owner`/`admin`/`operator`, `tenant` must be the exact external id,
  and for multi-tenant staff remember that a global `sub` unions their tenants — namespace it per tenant if that is not wanted (Contract §7).

## Integration disabled / key problems

* Every call answers `403 integration_disabled` → operator re-enables: `manage.py integration_set_active --slug <slug> --enable`.
* `key_revoked` after a rotation → the host still signs with the old key; deploy the new private key and `kid`. Check `IntegrationKey.last_used_at`
  before revoking next time.
* A pasted *private* key is refused at registration: give RastiChat only the **public** PEM.

## Deployment and start-up

* The process refuses to start with `ImproperlyConfigured`: read the message — it names the variable (`DEBUG=1` in production, missing
  `MONITORING_TOKEN`, wildcard `ALLOWED_HOSTS`, …). `python manage.py check --deploy --fail-level WARNING --tag security` lists the same issues.
* `ready` is `503` after deploy: run migrations; check `components` (database / redis / migrations).
* Dashboard shows wrong API host: `NEXT_PUBLIC_*` are baked at build time → rebuild.
* "Failed to load conversations" under normal load: nginx per-address limit shared by a NAT/CDN — see [`OPERATIONS.md`](OPERATIONS.md) §7.

## Still stuck?

Collect: the `request_id`, UTC time, the integration slug (never the token), the relevant log lines, and the output of
`GET /api/v1/health/ready/` (with the monitoring token for detail). Security-sensitive reports: [`SECURITY.md`](SECURITY.md) §1.
