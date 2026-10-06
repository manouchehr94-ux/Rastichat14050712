# Integrations — provisioning & security runbook

Operational procedures for the Integration Contract (see `docs/integrations/INTEGRATION_CONTRACT_V1.md`).
All commands run inside the backend container/venv (`python manage.py …`). **Never** run anything against a
production host as part of development; this runbook is for the operator who owns that deployment.

## Register a host application
1. The host generates its **own** Ed25519 keypair (`openssl genpkey -algorithm ed25519 -out host.private.pem`;
   `openssl pkey -in host.private.pem -pubout -out host.public.pem`). The private key stays in the host's secret
   store. **Do not generate production host keys on the RastiChat server** (`integration_keygen` is for development
   and reference hosts only).
2. `manage.py integration_create --slug my-app --name "My App" --platform-external-id <platform> [--scopes tenants:read,tenants:write]`
3. `manage.py integration_key_add --integration my-app --public-key-file host.public.pem [--scopes …] [--expires-at 2027-01-01T00:00:00Z]`
   → prints `kid=ick_…`; the host puts it in the JWS `kid` header. A private key pasted by mistake is refused.
4. Host calls `GET /api/v1/integrations/me/` (signed) to verify connectivity.

## Rotate a key (no downtime)
1. `integration_key_add` the new public key → new `kid`.
2. Deploy the host with the new private key + `kid`. Both keys verify during the overlap.
3. `integration_key_revoke --kid <old> --reason rotated`. Takes effect on the next request.
Recommended cadence: at least yearly, and on any suspected exposure or staff change.

## Emergency: suspected key compromise
1. `integration_key_revoke --kid <kid> --reason compromised` (immediate).
2. If more than one key is suspect or the scope is unclear: `integration_set_active --slug my-app --disable`
   (every endpoint refuses the integration; tenants and history are untouched).
3. Review `AuditEvent` rows with `action` starting `integration.` and the `integrations.security` log
   (`integration_token_refused`, `integration_scope_denied`) for the window.
4. Issue a new key (host generates a new pair), then `--enable`.
Assertions/API tokens live ≤ 120 s, so revocation closes the exposure within one TTL even for already-minted tokens
(single-use `jti` limits each to one use).

## Monitor
* Log lines: `integration_token_refused event=… code=… integration=… kid=…`, `integration_scope_denied`,
  `integration_replay_store_unavailable` (alert on this one: Redis is down and no integration call can succeed).
* Audit (`audit.AuditEvent`, `action` = `integration.*`): created/enabled/disabled, key_added/key_revoked,
  tenant_provisioned/tenant_updated/tenant_status_changed. Metadata holds identifiers and changed field *names* only.
* `IntegrationKey.last_used_at` (≤ 1 write/min) shows whether an old key is still in use before revoking it.

## Tunables (environment)
| Variable | Default | Meaning |
|---|---|---|
| `INTEGRATION_TOKEN_AUDIENCE` | `rastichat` | audience prefix; set different values per environment |
| `INTEGRATION_API_THROTTLE_RATE` | `300/min` | per-integration budget |
| `INTEGRATION_AUTH_FAILURES_PER_MINUTE` | `30` | failed authentications per client IP before 429 (0 = off) |

## Deprovisioning semantics
| Event | Effect | Data |
|---|---|---|
| tenant suspended / archived (`PUT status` / `DELETE`) | workspace+project inactive, visitor sessions revoked, staff REST/WS lose access on next check | retained |
| key revoked | tokens signed by it refused immediately | retained |
| integration disabled | every endpoint refuses it | retained |
| tenant restored (`PUT status: active`) | access resumes | — |
Permanent deletion/retention is a separate, explicitly approved operation; none is performed by these flows.
