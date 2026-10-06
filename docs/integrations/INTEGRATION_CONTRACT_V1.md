# RastiChat Integration Contract v1

Everything a host application needs to integrate RastiChat — **without reading RastiChat or any other host's source**.

Each section carries a status: **[implemented]** (code + tests in this repository), **[specified]** (the contract is
fixed here; implementation lands in the named slice). Sections marked *specified* may be refined only additively.

Conventions: JSON over HTTPS; times are ISO-8601 UTC; "tenant" = one customer organisation of the host (a store, an
account, an organisation…); "platform" = the host's own operating team.

---
## 1. Overview

```
 host backend (trusted)                       RastiChat                         browser
 ───────────────────────                      ─────────                         ───────
 PUT  /integrations/tenants/{t}   ─────────►  ensure workspace + project
 sign identity assertion (Ed25519) ───► (browser) ─────────────────────────────► POST assertion → session
                                                                                  widget / headless client ⇄ WS
```
Trust flows only from the host's **server** (holding the private key) to RastiChat. The browser carries opaque,
short-lived, signed material and is never a source of identity.

## 2. Versioning  **[implemented]**
* Contract v1 lives under `/api/v1/integrations/…`; every response carries `X-RastiChat-Contract: integration-v1`.
* Evolution is additive: new optional request fields, new response fields, new endpoints. Clients must ignore unknown
  response fields. A breaking change ships as `integration-v2` on new paths; v1 stays for at least 12 months after
  the v2 announcement, with deprecation announced via the `Deprecation`/`Sunset` response headers.
* Existing widget/dashboard endpoints are unchanged by this contract.

## 3. Authentication of the host (server-to-server)  **[implemented]**

### 3.1 Keys
The host owns an **Ed25519 keypair**. RastiChat stores only the **public key** (`kid`, PEM). Keys have scopes, optional
not-before/expiry, and can be revoked instantly. Multiple active keys may coexist (rotation). Registration and
rotation: `docs/runbooks/INTEGRATION_PROVISIONING_SECURITY.md`.

### 3.2 Request token
Every call carries `Authorization: Bearer <JWT>`:

JOSE header: `{"alg":"EdDSA","kid":"<kid>"}`

| claim | value |
|---|---|
| `iss` | integration slug |
| `sub` | integration slug (reserved for finer subjects) |
| `aud` | `<audience>:api` — default audience `rastichat`, i.e. `rastichat:api` (deployment-specific; see `INTEGRATION_TOKEN_AUDIENCE`) |
| `iat`, `exp` | issued-at / expiry; **`exp − iat ≤ 60 s`** (recommend 30 s); clock leeway 5 s |
| `jti` | unique per token (8–128 chars); **single use** — a replay is refused |
| `htm` | HTTP method, upper-case |
| `htu` | request **path** exactly as sent (no scheme/host/query), e.g. `/api/v1/integrations/tenants/shop-1/` |
| `bh` | base64url (no padding) of SHA-256 of the raw request body (`""` for no body) |

Mint a **new token per request** (also per retry). Example (Python):

```python
import base64, hashlib, json, time, uuid, jwt          # PyJWT[crypto]
def sign(private_pem, kid, slug, method, path, body=b""):
    now = int(time.time())
    return jwt.encode({
        "iss": slug, "sub": slug, "aud": "rastichat:api", "iat": now, "exp": now + 30, "jti": uuid.uuid4().hex,
        "htm": method, "htu": path,
        "bh": base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode(),
    }, private_pem, algorithm="EdDSA", headers={"kid": kid})
```

### 3.3 Scopes
`tenants:read`, `tenants:write` (this slice); reserved: `identity:customer`, `identity:staff`, `identity:platform`,
`conversations:initiate`, `events:receive`. Effective scopes = key scopes ∩ integration scopes.

### 3.4 Limits
Per-integration budget (default 300/min → `429 rate_limited`); repeated failed authentications from one IP are
throttled. Redis unavailable → `503 replay_store_unavailable` (fail closed).

## 4. Error model  **[implemented]**
```json
{"error": {"code": "tenant_archived", "message": "…", "details": {"…": "…"}, "request_id": "4c01d1c9622547a3"}}
```
`code` is stable; `message` is for humans; quote `request_id` in support requests. Codes:

| HTTP | code | meaning |
|---|---|---|
| 401 | `missing_token`, `invalid_token`, `token_expired`, `token_replayed`, `binding_mismatch`, `key_revoked` | authentication |
| 403 | `integration_disabled`, `scope_denied` | authorisation |
| 400 | `validation_error` (with `details` per field), `invalid_external_id` | request |
| 404 | `tenant_not_found` | also returned for another integration's tenant (no existence oracle) |
| 409 | `tenant_archived` | restore with `status: "active"` |
| 429 | `rate_limited` | back off (`Retry-After`) |
| 503 | `replay_store_unavailable` | retry later |

## 5. Idempotency & retries  **[implemented for provisioning; Idempotency-Key specified for PR E]**
* `PUT` and `DELETE` are idempotent by definition; retry freely (with a fresh token).
* Non-idempotent creations (e.g. platform-initiated conversation) take an `Idempotency-Key` header; the same key +
  same body returns the original result, the same key with a different body → `409 idempotency_conflict`.

## 6. Tenant provisioning  **[implemented]**

### `PUT /api/v1/integrations/tenants/{external_tenant_id}/`   (scope `tenants:write`)
`external_tenant_id`: 1–255 chars of `A-Za-z0-9 . _ : ~ -`, starting alphanumeric. Unique per integration.

```json
{
  "display_name": "Acme Shop",               // required on first call
  "verified_domains": ["shop.acme.com"],     // exact hostnames the host has verified (no wildcards), ≤ 50
  "status": "active",                        // "active" | "suspended"; omit to leave unchanged
  "defaults": {"branding": {"logo_url": "https://…", "subtitle": "…"}},   // seed-only
  "metadata": {"plan": "pro"}                // ≤ 2 KB, scalar values, never secrets (keys like *token*/*secret*… refused)
}
```
→ `201` (created) / `200`:
```json
{"integration":"acme","external_tenant_id":"shop-1","status":"active","created":true,
 "display_name":"Acme Shop","verified_domains":["shop.acme.com"],
 "workspace_id":12,"project_id":7,"project_public_key":"<uuid>","metadata":{},"updated_at":"…"}
```
* **Idempotent**: repeating creates nothing new. **Partial update**: only supplied fields change.
* Unknown fields → `400`. Archived tenant + no `status` → `409 tenant_archived`.
* `project_public_key` is the **public** widget project identifier (not a secret).

### `GET …/tenants/{id}/` (scope `tenants:read`) — same body. `GET /api/v1/integrations/me/` — who am I.
### `DELETE …/tenants/{id}/` (scope `tenants:write`) — archive (see §14). Idempotent.

### Ownership / merge rules
| Class | Fields | Rule |
|---|---|---|
| Integration-managed | `display_name`, `verified_domains`, `status`, `metadata` | overwritten when supplied; domains merged into the project's allowed domains — only entries this integration added can later be removed |
| Seed-only | `defaults.*` | applied at creation, never again |
| RastiChat-admin-managed | routing, queues, teams, canned replies, macros, SLA, automations, hand-added domains | never touched |
| Project-managed | launcher, branding, pre-chat (§9) | integration may only seed |

## 7. Identity assertions  **[specified — PR B]**
A host backend asserts "this browser belongs to this person, in this tenant, in this role". The browser then
exchanges the assertion for a RastiChat session. Browser-supplied ids are never trusted.

JWT, same keys and header as §3.2, with **`aud` = `<audience>:identity`**, `exp − iat ≤ 120 s` (recommend 60 s),
single-use `jti`, and:

| claim | meaning |
|---|---|
| `iss` | integration slug |
| `sub` | the host's stable id of the person (opaque; ≤ 255) |
| `tenant` | external tenant id (omitted only for `actor: platform_staff`) |
| `actor` | `customer` \| `tenant_staff` \| `platform_staff` |
| `role` | staff only, generic: `owner` \| `admin` \| `operator` (the host maps its own roles; RastiChat never learns them) |
| `origin` | optional: page origin the assertion is for; if present it must equal the request `Origin` |
| `name`, `email`, `avatar_url` | optional display data (minimise; email only if verified by the host) |
| `ctx` | optional small context snapshot (§11) |
| `scp` | optional scopes; must be ⊆ the key's `identity:*` scopes |

Exchanges (CORS-enabled for the project's allowed domains; per-IP and per-subject rate limited):
* `POST /api/v1/identity/customer/ {project_key, assertion}` → visitor session (same shape as `widget/init`).
  Requires scope `identity:customer`; the tenant in the assertion must own `project_key`.
* `POST /api/v1/identity/staff/ {assertion}` → dashboard JWT pair + memberships. Scope `identity:staff`.
  Staff users and memberships are created/updated idempotently and marked integration-managed.
  *Delivery to a dashboard page: URL fragment or POST body — never a query string.*
* `actor: platform_staff` → `PlatformMembership` (scope `identity:platform`).

Guest ↔ authenticated: a guest visitor is never merged by an external id alone. Upgrade requires a valid assertion
**and** the guest's own session token; the guest conversation is attached only to that session's visitor.

## 8. Deprovisioning & revocation  **[tenant/key/integration: implemented; user/membership: specified — PR B]**
| Event | Call | Effect |
|---|---|---|
| tenant deactivated | `PUT … status:"suspended"` | workspace+project inactive; visitor sessions revoked; staff lose access on next check; sockets close |
| tenant archived | `DELETE …/tenants/{id}/` | as above, status `archived` |
| tenant restored | `PUT … status:"active"` | access resumes |
| membership removed *(PR B)* | `DELETE …/tenants/{id}/members/{user}/` | membership deleted; their sockets close on next revalidation |
| user disabled *(PR B)* | `POST …/users/{user}/disable/` | all integration-managed memberships removed; sessions revoked |
| key revoked | operator CLI | tokens by that key refused immediately |
| integration disabled | operator CLI | every endpoint refuses |

History (conversations, messages, attachments) is **retained** in every case; deletion is a separate retention operation.

## 9. Widget configuration & pre-chat  **[specified — PR C]**
`GET /api/v1/widget/config/?project_key=<public key>` (public, Origin-checked) returns a versioned document:
```json
{"version": 1,
 "launcher": {"enabled": true, "mode": "icon|icon_text", "position": "bottom-right|bottom-left", "offset": {"x":16,"y":16},
              "label": "…", "tooltip": "…", "icon": "chat", "color": "#…", "greeting": "…", "auto_open": false,
              "mobile": {"fullscreen": true}, "hide_on": []},
 "pre_chat": {"enabled": true, "fields": [ {"key":"topic","type":"select","label":"…","required":true,"order":1,
                                              "choices":[{"value":"order","label":"…"}], "max_length":null} ]},
 "identity": {"guest_allowed": true, "authenticated_only": false},
 "branding": {"name":"…","logo_url":"…","subtitle":"…"}, "locale":"fa", "direction":"rtl", "capabilities": {}}
```
Field types: `text`, `textarea`, `email`, `phone`, `select`, `radio`, `checkbox`, `consent`, `hidden`. Validated
server-side; answers are stored with the conversation as structured data (not columns) and are visible to the receiving
operator, routing, automations and events. Modes: no questions / one question / structured form — pure configuration.

## 10. Widget & headless clients  **[specified — PR C/D]**
* Widget: `RastiChat.init({project, bootstrap: async () => fetchAssertionFromMyBackend()})` — see `docs/widget/EMBEDDING.md`.
  The project key is public; identity only via `bootstrap`; no long-lived credential in markup.
* Headless: the same REST + WebSocket protocol the widget uses (OpenAPI at `/api/schema/`), plus a small
  TypeScript client in `packages/`. Realtime always via single-use tickets (`/widget/ws-ticket/`), never URL credentials.

## 11. Context  **[specified — PR B/C]**
Hosts **push** small, tenant-scoped, minimised snapshots (customer name, tier, current page, order reference…) either in
the assertion `ctx` claim or via `PUT /api/v1/integrations/tenants/{t}/contexts/{subject}/`. Operators see them in the
conversation sidebar. RastiChat never calls back into the host database. No credentials; ≤ 4 KB; classified as
`identity`, `profile`, `context` or `sensitive` (sensitive is not stored unless the project opts in).

## 12. Platform ↔ tenant conversations  **[specified — PR E]**
Generic directions: tenant admin → platform support (via staff assertion, normal widget/dashboard flow) and
**platform → tenant admin** (platform opens a conversation with a chosen tenant without the tenant writing first):
`POST /api/v1/integrations/tenants/{t}/support-conversations/` (scope `conversations:initiate`, `Idempotency-Key`),
one active thread per `(tenant, subject_key)`; the tenant's admins are notified and reply in their support inbox.

## 13. Webhooks / events  **[specified — delivery follows PR D]**
Signed (Ed25519, RastiChat deployment key published at `/.well-known/rastichat-jwks.json`), at-least-once, bounded
exponential backoff, `event_id` for consumer idempotency, `version`, tenant + integration identity, minimal PII, no
secrets. Callback URLs: https only, resolved-IP checked against private/link-local ranges, no redirects, fixed
timeouts (SSRF hardening). Events: `conversation.created|assigned|closed|reopened`, `message.created`,
`attachment.created`, `rating.submitted`, `support_conversation.created`.

## 14. Lifecycle summary of a tenant
`active` ⇄ `suspended`; `active|suspended` → `archived` (via `DELETE`) → `active` (explicit restore).

## 15. Security requirements for hosts
1. Keep the private key in a secret store; never ship it to browsers, repos or logs. Rotate yearly / on exposure.
2. Mint a fresh token per request; TTL ≤ 60 s (api) / 120 s (identity); never log tokens.
3. Derive tenant, user and role **server-side** from your own session — never from request parameters.
4. Register only exact verified domains; keep CSP `connect-src`/`frame-src` for the RastiChat origin.
5. Treat chat as optional UI; fail closed on assertion errors (show guest mode or hide the launcher).

## 16. Checklist: integrate a new application
1. Generate an Ed25519 keypair; send the **public** key to the RastiChat operator → `kid`.
2. Implement a `sign()` helper (§3.2) and `PUT` your first tenant (§6). Store `project_public_key`.
3. Add allowed domains (`verified_domains`).
4. Configure launcher/pre-chat (§9).
5. Add a backend endpoint that returns a fresh identity assertion for the logged-in user (§7).
6. Embed the widget (§10) — or use the headless client.
7. Subscribe to events if needed (§13).
8. Wire user/tenant lifecycle to §8.
