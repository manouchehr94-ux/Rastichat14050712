# Security

How RastiChat protects tenants, customers and host applications — and exactly where the trust boundaries are.
Each statement below is backed by code and by tests in this repository (the test names are given where useful); where a limitation exists it
is stated.

## 1. Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use the repository host's *private vulnerability reporting* / security-advisory
feature ("Security" tab → "Report a vulnerability") if it is enabled. If it is not, contact the repository owners through the contact
channel published on the repository's profile page. Include affected version/commit, impact, reproduction steps and whether you believe
the issue is already being exploited.

> This repository intentionally publishes no personal or shared mailbox. The maintainers of a deployment should add their own
> reporting address here before going live.

## 2. Trust boundaries

```
 host backend (holds private key)     ← trusted for: which tenant, which person, which role
 RastiChat backend + PostgreSQL + Redis ← trusted core
 browser / mobile app / any client    ← NEVER trusted for identity, tenant or role
```

* **Identity is asserted only by a host's server.** A browser-supplied `external_id`, `user_id`, `tenant_id`, `role` or project key is
  never proof of anything. The only way to become a *verified* customer or a staff member is a signed assertion that the backend
  verifies (§4). The legacy unverified-`external_id` lookup is **off** and cannot be turned on in production without an explicit
  acknowledgement variable; a verified visitor id (`int:<slug>:<sub>`) can never be claimed through that path.
* **Tenant scope is derived server-side** from the signed assertion / the integration's mapping — never from request parameters.
  A project key is a *public* identifier, not a credential.
* RastiChat has **no shared secret with hosts**. It stores **public keys only**; a database or backup leak cannot be used to forge host
  tokens. The Django `SECRET_KEY` is never used for integration auth.

## 3. Tenant isolation

* Every tenant is a workspace reached only through an explicit `(integration, external tenant id)` mapping.
* One integration can never see or address another's tenants: tenant lookups are filtered by the authenticated integration and a foreign
  tenant answers the **same** `404` as a missing one (no existence oracle). The suites in `backend/integrations/tests*.py` and `backend/conversations/tests_support_threads.py`
  cover cross-integration, cross-tenant, cross-platform and admin-of-A-who-is-operator-of-B scenarios.
* Staff authorisation is by *exact* workspace membership and role. An admin role in workspace B never grants anything in A.
* Customer sessions belong to one project; presenting a session on another tenant's conversation answers 404/403.
* **Multi-store users:** a staff `sub` is global to an integration, and so is the RastiChat account created for it; a person with access to
  several tenants therefore sees the union of those inboxes. Hosts that need strict per-tenant separation must namespace the staff `sub`
  per tenant (e.g. `u42.<tenant id>`), which gives each (person, tenant) its own isolated account. This is documented in Contract §7 and
  exercised end-to-end.
* Platform ↔ tenant threads are visible only to the tenant's own Owner/Admins and to staff of the platform that owns the tenant.

## 4. Integration authentication (host → RastiChat)

* **Ed25519** (JWS `EdDSA`) with `kid` lookup. The algorithm is pinned and the stored key is parsed as an Ed25519 *public key object*, so
  `alg:none` and HMAC-with-the-public-key confusion are refused (tested).
* Claims: `iss` = integration slug, purpose-specific `aud` (`<audience>:api` ≠ `<audience>:identity`), mandatory `exp`/`iat`/`jti`/`sub`,
  TTL caps (API 60 s, identity 120 s), 5 s clock leeway.
* **Replay protection:** every `jti` is single-use (Redis `SET NX` with TTL). **Fail closed:** if Redis is unavailable the token is refused
  with `503 replay_store_unavailable`, never accepted unchecked.
* **Request binding:** API tokens carry `htm` (method), `htu` (path) and `bh` (SHA-256 of the body): a captured token cannot be redirected to
  another endpoint or combined with another body — and it is single-use anyway.
* **Least privilege:** scopes on the integration, narrowed per key; effective scope = intersection. Endpoints enforce scopes individually.
* **Key rotation:** add the new public key → switch the host → revoke the old (`integration_key_revoke`); both verify during the overlap;
  revocation is effective on the *next request* (keys are looked up every time). `IntegrationKey.last_used_at` shows whether an old key is
  still in use. Cadence: at least yearly and on any suspected exposure. A pasted *private* key is refused at registration.
  Emergency: revoke the key, and if scope is unclear `integration_set_active --disable` (every endpoint refuses the integration; data untouched).
* **Throttling:** per-integration budget (default 300/min) and a per-client-address limit on *failed* authentications.
* **Audit:** provisioning, key and identity events are recorded with identifiers and changed *field names* only — never values.

## 5. Browser-facing credentials

* **Visitor sessions** — opaque random tokens, sliding expiry (30 days) with an absolute limit (180 days), rotation after 24 h, explicit
  revoke on logout, instant revocation when a tenant is disabled or a customer is disabled. Sent in the `X-Widget-Session` header,
  never in a URL.
* **Staff sessions from SSO** — a normal dashboard JWT with a short life (default 30 min) and **no refresh token**; the host's next assertion
  is the renewal. Assertions are delivered to the dashboard in the URL **fragment** (never the query string), cleared immediately,
  with an open-redirect-safe `next`.
* **WebSocket tickets** — random 256-bit, stored only as a SHA-256 hash in Redis, TTL 30 s, **consumed atomically (single use)**, bound to
  *kind*, *subject* and the *exact conversation*; a replay elsewhere is refused and burns the ticket. The socket accepts only
  `{"type":"auth","ticket":…}` as its first frame (closed `4401` otherwise, or after 10 s of silence) and joins **no** channel group before that.
  There is no credential in any WebSocket URL on staging/production. See [`WEBSOCKET_PROTOCOL.md`](WEBSOCKET_PROTOCOL.md).
* **Live revocation** — authorisation is re-checked before every inbound frame and delivered event (memoised for 5 s) **and** on a timer for
  idle sockets (30 s ± jitter); a removed member, disabled user, revoked session, deactivated tenant or removed allowed domain closes the
  socket with `4403` and stops delivery.
* **Rate limits** — DRF scoped throttles on every public/mutating endpoint, a Redis limiter on WebSocket *messages* (widget 30/min, staff
  60/min), nginx limits on login, WebSocket handshakes and general traffic.

## 6. Allowed domains, Origin and CORS

* `Project.allowed_domains` (exact hosts, optional ports, `*.sub` wildcards; never `*`) gate which **websites** may use a project key.
  Creating a visitor session requires a matching `Origin` (a request without one is refused when domains are configured); established
  sessions are authorised by their own credential. The WebSocket handshake `Origin` must match the **session's own project** and is
  re-checked live.
* This is a defence against *other websites'* browsers, not authentication: a non-browser client can forge `Origin`. Real identity always
  comes from assertions (§2).
* CORS: dashboards and admin APIs are only opened to the explicit `CORS_ALLOWED_ORIGINS` list (never a wildcard outside development).
  **Only** widget-facing endpoints (`/api/v1/widget/`, `/api/v1/kb/public/`, `/api/v1/identity/customer/`, `/ws/widget/`, `/ws/v2/widget/`) additionally
  accept origins of active projects' allowed domains — and the per-project check remains the real gate.
* `WIDGET_REQUIRE_ALLOWED_DOMAINS=1` refuses projects that have no domains at all. See [`runbooks/PROJECT_ALLOWED_DOMAINS.md`](runbooks/PROJECT_ALLOWED_DOMAINS.md).

## 7. Private attachments

Chat attachments are never public files. Each URL carries a signed token (audience: staff user, one visitor session or the conversation's
visitor; bound to one message; TTL 10 min) **and Django re-authorises the requester's current access on every fetch** (membership, session,
tenant active…) before nginx streams the file via an `internal` location. Any refusal is one uniform `404`. Removing a member or revoking a
session therefore cuts off *new* fetches immediately, not when the URL expires. Responses are `private, no-store`, `nosniff`, `no-referrer`.
Uploads are type-, size- and signature-validated ([`security/MEDIA_UPLOAD_SECURITY.md`](security/MEDIA_UPLOAD_SECURITY.md)).

## 8. Logging and redaction

* **Never logged:** passwords, JWTs, assertions, WebSocket tickets, visitor session tokens, signed attachment tokens, host private keys,
  full message bodies, the monitoring token.
* nginx logs with the `rastichat_redacted` format: no query string, credential path segments (`/ws/widget/<token>/…`, `/media/attachments/…`)
  become `[redacted]`. Daphne's own access log is wrapped (`config/daphne_server.py`) to the same rules. Both are proven by tests
  (`scripts/nginx/test-log-redaction.sh`, `config/tests_daphne_access_log.py`).
* Structured events (`rastichat_event event=… label=… k=v`) drop credential-named fields and sanitise/truncate values.
* Every request gets a correlation id (`X-Request-ID`) for tracing without logging content.
* Errors use a uniform envelope; foreign-tenant and missing-resource cases are indistinguishable.

## 9. Data minimisation

Hosts push only what they choose: a pseudonymous id, an optional display name, and an optional flat, ≤ 4 KB context snapshot. RastiChat
refuses credential-like keys and values, JWT-shaped strings, control characters and any "sensitive" section. Host-supplied text is rendered
as text, never markup. Classification and retention rules: [`architecture/INTEGRATION_PLATFORM.md`](architecture/INTEGRATION_PLATFORM.md).

## 10. Deprovisioning and revocation (what stops, how fast)

| Action | Effect | Latency |
|---|---|---|
| revoke an integration key | tokens by that key refused | next request |
| disable an integration | every integration endpoint refuses | next request |
| suspend/archive a tenant | workspace+project inactive; visitor sessions revoked; sockets closed | next check (≤ 5 s on events, ≤ ~36 s idle) |
| remove a staff membership | REST refuses; sockets close `4403`; attachment fetches refused | immediate (REST) / ≤ revalidation (sockets) |
| disable a staff user | account deactivated (even unexpired JWTs die) | immediate |
| disable a customer | sessions revoked, exchanges refused | immediate |

History is retained in every case; erasure is an explicit, operator-approved retention procedure.

## 11. Deployment guardrails

In `staging`/`production` the process refuses to start with `DEBUG=1`, a missing `DJANGO_SECRET_KEY`, a missing/empty database password or the published development one (`DB_PASSWORD`, or the password inside `DATABASE_URL`; the development-only default exists only when `ENVIRONMENT=development`), wildcard `ALLOWED_HOSTS`,
no `MONITORING_TOKEN`, or the legacy credential-in-URL switches without their acknowledgements, and the deploy gate rejects a weak secret key; `manage.py check --deploy --fail-level WARNING
--tag security` is the CI/deploy gate. CI also runs gitleaks over the full history, Bandit and `pip-audit`.

## 12. Host-side requirements (summary)

Keep the private key in a secret store; mint a fresh token per request (TTL ≤ 60 s / 120 s); derive tenant, user and role from your own
session; register only exact verified domains; treat chat as optional UI and fail closed (guest or hidden) on assertion errors; never log
tokens. The full list is Contract §15.

## 13. Known limitations

* No webhook/event delivery in v1, so there is no outbound request surface (and no SSRF exposure) — see
  [`integrations/V1_SCOPE_AND_DEFERRALS.md`](integrations/V1_SCOPE_AND_DEFERRALS.md).
* Conversations still *assigned* to a removed member keep that assignee until an admin reassigns them.
* Internal-visibility knowledge-base attachments are served by their public path (documented in `STAGING_DEPLOYMENT.md`).
* The per-address nginx limits assume nginx sees real client addresses.
