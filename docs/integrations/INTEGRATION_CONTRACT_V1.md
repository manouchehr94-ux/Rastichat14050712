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
`tenants:read`, `tenants:write` (provisioning), `identity:customer`, `identity:staff`, `identity:platform` (identity and
membership management), `conversations:initiate` (§12), `context:write` (§11). Reserved, not yet used: `events:receive` (§13).
Effective scopes = key scopes ∩ integration scopes. Grant a key only what that host process needs.

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
| 403 | `integration_disabled`, `scope_denied`, `tenant_unavailable`, `tenant_mismatch`, `identity_disabled`, `origin_not_allowed`, `origin_required`, `origin_mismatch`, `initiator_not_authorized` | authorisation |
| 400 | `validation_error` (with `details` per field), `invalid_external_id`, `invalid_assertion`, `invalid_project`, `idempotency_key_required`, `empty_message`, `message_too_long` | request |
| 404 | `tenant_not_found`, `identity_not_found` | also returned for another integration's tenant (no existence oracle) |
| 409 | `tenant_archived` (restore with `status: "active"`), `idempotency_conflict`, `thread_already_active` | |
| 429 | `rate_limited` | back off (`Retry-After`) |
| 503 | `replay_store_unavailable` | retry later |

## 5. Idempotency & retries  **[implemented]**
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

## 7. Identity assertions  **[implemented — PR B]**
A host backend asserts "this browser belongs to this person, in this tenant, in this role". The browser then
exchanges the assertion for a RastiChat session. Browser-supplied ids are never trusted.

JWT, same keys and header as §3.2, with **`aud` = `<audience>:identity`**, `exp − iat ≤ 120 s` (recommend 60 s),
single-use `jti`. No request-binding claims (the browser relays it). Claims:

| claim | meaning |
|---|---|
| `iss` | integration slug |
| `sub` | the host's stable id of the person (opaque; same charset as external ids) |
| `actor` | `customer` \| `tenant_staff` \| `platform_staff` (needs scope `identity:customer` \| `identity:staff` \| `identity:platform`) |
| `tenant` | external tenant id (required for `customer` and `tenant_staff`; absent for `platform_staff`) |
| `role` | staff only, **generic**: `owner` \| `admin` \| `operator`. The host maps its own roles; RastiChat never learns them. Mapped to workspace owner/admin/operator or platform owner/admin/support-agent |
| `origin` | optional: page origin the assertion is for; when present it must equal the request `Origin` (`origin_mismatch`) |
| `name` | optional display name (minimise PII). Email/phone are **not** accepted as identity |

### Customer exchange — `POST /api/v1/identity/customer/`
`{"project_key": "<public key>", "assertion": "<jwt>"}` → `200`
`{"visitor_id", "session_token", "expires_at", "identity": {"verified": true, "conversations_attached": 0}}`
(the session is an ordinary visitor session: same expiry/rotation/revocation, header `X-Widget-Session`, WS tickets).
* CORS-enabled for the project's allowed domains; the project's domain policy is enforced (`origin_required` /
  `origin_not_allowed`), exactly as for guest `widget/init`.
* The project must belong to **the same integration and tenant the assertion names** (`tenant_mismatch`), so a valid
  assertion for tenant A is useless on tenant B's project, and another integration's assertion is useless here.
* The same `(integration, tenant, sub)` always resolves to the same visitor → history resumes. The same person in two
  tenants is two isolated visitors. `Visitor.external_id` of verified customers is `int:<slug>:<sub>`; the legacy
  browser-supplied-`external_id` path can never claim such a value.

### Staff exchange — `POST /api/v1/identity/staff/`
`{"assertion": "<jwt>"}` → `{"access", "expires_in", "user", "workspace_id", "memberships", "platform_roles"}`.
* A **dedicated** RastiChat user (unusable password, synthetic email) is created on first sight and linked to
  `(integration, sub)`. An existing RastiChat user is **never** linked, matched by email, or modified.
* The membership for the asserted tenant (or the integration's platform for `platform_staff`) is created/updated
  idempotently and recorded as integration-owned. Roles are re-asserted on every exchange.
* **A staff `sub` is global to the integration, and so is its RastiChat account.** If one person has staff access to
  several tenants, that account holds a membership in each and the dashboard shows the union of those inboxes. Hosts
  that want a session entered through tenant B to be unable to see tenant A (the usual choice for merchant-facing
  products) should namespace the staff `sub` per tenant (e.g. `u42.<tenant id>`): each (person, tenant) then has its own
  isolated account. Remember to use the same ids with `members/{user}/` and `users/{user}/disable/`.
* The access token is a normal dashboard JWT with a short lifetime (`INTEGRATION_STAFF_SESSION_MINUTES`, default 30) and
  **no refresh token**: the host's next assertion is the renewal. Deliver the assertion to a dashboard page in the
  **URL fragment or a POST body — never the query string**.

### Guest → authenticated upgrade
Present the assertion **and** the guest's own `X-Widget-Session` on the customer exchange. Only then are that guest's
conversations (same project) attached to the verified visitor and the guest session revoked. An external id alone never
merges anything; an assertion without the guest credential attaches nothing; a guest from another project is never
touched; if the verified visitor already has an open conversation the guest's open one stays with the retired guest
record (never two open threads for one visitor).

## 8. Deprovisioning & revocation  **[implemented — tenant/key/integration (PR A), user/membership/customer (PR B)]**
| Event | Call (scope) | Effect |
|---|---|---|
| tenant deactivated | `PUT …/tenants/{t}/ {"status":"suspended"}` (`tenants:write`) | workspace+project inactive; visitor sessions revoked; staff REST/WS refuse on next check |
| tenant archived | `DELETE …/tenants/{t}/` | as above, status `archived` |
| tenant restored | `PUT … {"status":"active"}` | access resumes |
| staff role sync / pre-provision | `PUT …/tenants/{t}/members/{user}/ {"role","display_name"?}` (`identity:staff`) | dedicated account + membership ensured |
| membership removed | `DELETE …/tenants/{t}/members/{user}/` (`identity:staff`) | integration-owned workspace membership **and** team roles removed; REST refuses immediately, sockets close on next revalidation; `{"removed": false}` if nothing to remove |
| platform staff sync / removal | `PUT\|DELETE …/platform/members/{user}/` (`identity:platform`) | platform membership |
| staff user disabled | `POST …/users/{user}/disable/` (`identity:staff`) | account deactivated (even unexpired JWTs die), all integration-owned memberships removed; `…/enable/` re-activates (access returns only on the next assertion) |
| customer disabled | `POST …/tenants/{t}/customers/{user}/disable/` (`identity:customer`) | visitor sessions revoked, exchanges refused; `…/enable/` |
| key revoked | operator CLI | tokens by that key refused immediately |
| integration disabled | operator CLI | every endpoint refuses |

An integration can only change/remove memberships **it created**; another integration (or a manually-added member) is
untouched even with identical ids. History (conversations, messages, attachments) is **retained** in every case; deletion
is a separate retention operation. Known limitation: conversations still *assigned* to a removed member keep that
assignee until an admin reassigns them.

## 9. Widget configuration & pre-chat  **[implemented — PR C]**
`GET /api/v1/widget/config/?project_key=<public key>` (public, Origin-checked) returns a versioned document (shape abridged; the normative schema is in `docs/widget/PRE_CHAT_CONFIGURATION.md`):
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
operator, routing and automations (`conversation.pre_chat`) and — once webhooks land — events. Modes: no questions / one
question / structured form — pure configuration. Full schema, limits and examples: `docs/widget/PRE_CHAT_CONFIGURATION.md`.
An integration may seed it at provisioning with `defaults.widget` (applied once; see §6 ownership). Projects that
never set a configuration keep the original widget behaviour.

## 10. Widget & headless clients  **[implemented — widget PR C; reference host PR D; headless: `HEADLESS.md`]**
* Widget: `RastiChat.init({project, bootstrap: async () => fetchAssertionFromMyBackend()})` — see `docs/widget/EMBEDDING.md`.
  The project key is public; identity only via `bootstrap`; no long-lived credential in markup.
* Headless: the same REST + WebSocket protocol the widget uses (OpenAPI at `/api/schema/`); see `HEADLESS.md` and the runnable
  `examples/headless/headless.mjs`. No SDK package is shipped (decision recorded in `V1_SCOPE_AND_DEFERRALS.md`).
  Realtime always via single-use tickets (`/widget/ws-ticket/`), never URL credentials.

## 11. Context  **[implemented]**
Hosts **push** small, tenant-scoped, minimised snapshots about a *verified customer* — either in the optional `ctx` claim of the
customer assertion or with `PUT /api/v1/integrations/tenants/{t}/contexts/{customer}/` (scope `context:write`). RastiChat never
calls back into the host database. Operators see the snapshot in the conversation sidebar (`customer-context` → `host_context`).

```json
PUT /api/v1/integrations/tenants/shop-1/contexts/cust-42/
{ "profile": {"tier": "gold", "orders": 12},      // display data about the person
  "context": {"page": "/cart", "order_ref": "A-1001"} }   // what the conversation is about now
→ 200 {"external_user_id": "cust-42", "profile": {...}, "context": {...}, "updated_at": "..."}
```
* **Replace semantics:** PUT replaces the whole snapshot (idempotent; keys you omit are removed). `GET` reads it, `DELETE` removes it
  (204, also when none exists).
* **Strict shape:** flat keys `^[a-z][a-z0-9_]{0,39}$`; values are strings (≤200 chars), numbers or booleans — no nesting, no `null`;
  ≤20 keys per section; ≤4 KB in total. Violations → `400 invalid_context` / `context_too_large`.
* **No credentials, no sensitive data:** key names that look like credentials/payment data (`password`, `token`, `secret`, `api_key`,
  `cookie`, `session`, `card`, `cvv`, `iban`, `otp`…), JWT-like values, control characters and any `sensitive` section are refused with
  `400 sensitive_context_not_accepted`. v1 has no "sensitive" tier at all.
* **Tenant-scoped:** the snapshot belongs to one customer identity of one tenant of *your* integration; another integration or tenant
  gets the uniform `403 tenant_unavailable`. A guest or a browser-claimed id never has host context.
* **Lifecycle:** it lives as long as the customer identity; disabling the customer blocks further pushes (`403 identity_disabled`);
  delete it with `DELETE` (data-subject requests) — history of conversations is governed by the retention rules in
  `INTEGRATION_PLATFORM.md`.
* **Audit:** the audit trail records *which keys* were written, never the values.

## 12. Platform ↔ tenant conversations  **[implemented — PR E]**
One conversation engine, two directions, generic platform/tenant semantics ("platform" = the operator of the integration's
`Platform`; "tenant" = a mapped workspace):

* **tenant admin → platform support**: a tenant Owner/Admin (staff assertion, normal dashboard) uses `POST /api/v1/support/start/`
  (create-or-resume) or `POST /api/v1/support/` (always a new ticket); platform staff answer from the platform inbox.
* **platform → tenant admin** (the tenant never has to write first):
  * in the platform dashboard: *"گفتگوی جدید با سازمان"* → `POST /api/v1/platform/support/start/` (platform **Owner/Admin**;
    the workspace must belong to the caller's platform — otherwise a uniform 404; support agents may reply but not initiate;
    `GET /platform/support/workspaces/` feeds the picker);
  * from the host's trusted backend (server-to-server):
    `POST /api/v1/integrations/tenants/{t}/support-conversations/` — scope `conversations:initiate`, **`Idempotency-Key`
    required**, body `{"initiator_user_id", "subject"?, "subject_key"?, "message"}`. `initiator_user_id` must be an active
    **owner/admin platform member this integration created** (assertion or `PUT /platform/members/{id}/`); the message is
    authored by that RastiChat user. → `201 {"conversation_id","created":true,"status","subject_key","opened_by":"platform"}`.
* **Idempotency**: at most ONE active thread per `(tenant, subject_key)` (default `general`; a partial unique constraint
  decides races): repeating a start **resumes** it and appends the message. `client_message_id` (or the integration's
  `Idempotency-Key`) makes retries safe; the same `Idempotency-Key` with a different request → `409 idempotency_conflict`;
  a retry replays the first response (`Idempotent-Replayed: true`). A **closed** thread is never silently reused — a new
  start opens a fresh one; a *reply* to a closed thread reopens it.
* **Lifecycle**: `POST …/close/`, `…/reopen/` on both sides (reopen refuses `409 thread_already_active` if another thread
  on the same subject is open). Status says who owes the next answer (`WAITING_FOR_WORKSPACE` / `WAITING_FOR_PLATFORM`).
* **Realtime & unread**: messages travel on `support_chat_<id>` (frames carry `sender_side: platform|tenant`); the other
  side gets an in-app notification (`SUPPORT_MESSAGE`: tenant admins for platform messages, platform staff for tenant
  messages; never the sender, never tenant operators); a thread's own messages are not "unread" for their author.
* **Isolation**: tenant admins see only their workspace's threads (exact-workspace admin check — admin of A who is only an
  operator of B sees nothing of B); platform staff only their platform's tenants; another integration can never address
  this tenant. Audit: `support_conversation_created|closed|reopened` (identifiers only).
Operator tooling: `manage.py integration_purge_idempotency --older-than-hours 48` (cron).

## 13. Webhooks / events  **[designed; delivery deliberately deferred — see `V1_SCOPE_AND_DEFERRALS.md`]**
The scope `events:receive` is reserved. The design below is the contract future delivery will follow; **v1 does not send events.**
Signed (Ed25519, RastiChat deployment key published at `/.well-known/rastichat-jwks.json`), at-least-once, bounded
exponential backoff, `event_id` for consumer idempotency, `version`, tenant + integration identity, minimal PII, no
secrets. Callback URLs: https only, resolved-IP checked against private/link-local ranges at connect time, no redirects, fixed
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
