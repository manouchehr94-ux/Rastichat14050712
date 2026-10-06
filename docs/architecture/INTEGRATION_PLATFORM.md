# RastiChat Integration Platform — architecture

> Status: living document. **Implemented** = merged-ready code with tests in the PR named; **Specified** = contract
> text exists, code lands in the named PR. See the status table at the end.

## Principle

> RastiChat is a standalone, reusable, multi-tenant communication platform. Host applications integrate through
> stable generic contracts. RastiSi is an adapter, not the domain model of RastiChat.

Everything below is host-agnostic. The words "RastiSi", "store", "order" appear only in `docs/integrations/RASTISI.md`
and in the RastiSi repository. The acceptance test for every design decision is: *could an unrelated SaaS integrate
tomorrow using only these documents and configuration, with no change to RastiChat core?*

## Concept map

| Generic concept | RastiChat representation | Host supplies |
|---|---|---|
| Integration (a host application) | `integrations.Integration` (+ `IntegrationKey`, public keys only) | slug, public key(s) |
| Platform (the SaaS owner) | existing `platforms.Platform` | — (chosen at registration) |
| Tenant | `workspaces.Workspace` via `IntegrationTenantMapping` | external tenant id, display name, verified domains |
| Project (a widget deployment) | existing `projects.Project` (one default per mapping) | — |
| Customer / visitor | existing `visitors.Visitor` (+ `ExternalIdentity` in PR B) | trusted assertion |
| Staff / operator | existing `accounts.User` + `WorkspaceMembership` (+ `ExternalIdentity`) | trusted assertion (generic role) |
| Platform actor | existing `PlatformMembership` | trusted assertion |
| Conversation / message / routing / SLA … | existing engine — **unchanged** | — |

The mapping table is the *only* place an external identifier lives. Core chat models carry no host columns.
(`Workspace.external_id` / `Platform.external_id` pre-date this work and are not used as a source of truth.)

## Decisions

### D1. Reuse the conversation engine; add a thin integration layer
An embedded integration is just another way to create a `Visitor`/`User` and a `Workspace`. Messages, inbox, routing,
SLA, automations, receipts, attachments and WebSocket protocol are the existing ones. No second simplified backend.

### D2. Asymmetric (Ed25519) signing, public keys only at rest
Host → RastiChat trust is established by JWTs signed with the host's **Ed25519 private key** (JWS `EdDSA`).
RastiChat stores **only public keys**.

Why not a shared HMAC secret:
* A database/backup leak at RastiChat would let an attacker forge assertions for every integration; with public keys
  it cannot.
* No secret has to be transported to, or stored encrypted by, RastiChat; rotation never requires coordinating a
  shared value (add key → switch → revoke).
* `Django SECRET_KEY` is never involved (and must never be used for this).

Cost: the host needs an Ed25519-capable JOSE library (available for every mainstream language; in Python
`PyJWT[crypto]`/`cryptography`). Algorithm confusion is closed by pinning `EdDSA` and parsing the stored value as an
Ed25519 *public key object* (an HS256 token "signed" with the public key is rejected — tested).

One verifier (`integrations/tokens.py`) serves every token: pinned algorithm, `kid` lookup, active key + active
integration, `iss == integration.slug`, purpose-specific `aud` (`<audience>:api` ≠ `<audience>:identity`),
mandatory `exp/iat/jti/sub`, TTL cap per purpose (api 60 s, identity 120 s), clock leeway 5 s, **single-use `jti`**
(Redis `SET NX`, fail-closed — if Redis is down the token is refused with 503, never accepted).

### D3. Request-bound server-to-server tokens
API tokens additionally carry `htm` (method), `htu` (path) and `bh` (SHA-256 of the body). A captured token cannot
be replayed (single use), re-targeted at another endpoint, or combined with another body.

### D4. Least privilege via scopes
`Integration.scopes` is what the integration may ever do; `IntegrationKey.scopes` narrows per key. A request is
authorised for the intersection. (`tenants:read|write` now; `identity:*`, `conversations:initiate`, `events:receive` reserved.)

### D5. Provisioning is an idempotent PUT with explicit field ownership
See "Configuration ownership" below. Repeating a call never duplicates; omitted fields are untouched; manual
RastiChat configuration is never overwritten.

### D6. Lifecycle reuses existing revocation machinery
Suspending/archiving a tenant sets `Workspace.is_active=False` and `Project.is_active=False` and revokes its visitor
sessions. Existing REST/WebSocket/session code already refuses inactive workspaces/projects and re-validates live
sockets, so prompt cut-off needs no new enforcement path. History is **retained**; deletion is a separate retention
operation, never a side effect of deprovisioning.

### D7. No new infrastructure
Same Django/DRF/Channels/PostgreSQL/Redis. No queue, microservice or extra database. (Webhook delivery, when added,
uses the existing scheduler-worker pattern.)

## Configuration ownership

| Class | Fields | Rule |
|---|---|---|
| Integration-managed | external tenant id (mapping key, immutable), `display_name` (workspace + default project name), `verified_domains`, tenant `status`, `metadata` | overwritten **only when supplied**; domains merged into `Project.allowed_domains`, and only entries the mapping itself added may later be removed |
| Seed-only | `defaults.branding` (logo, subtitle); later launcher/pre-chat defaults | applied at creation, never re-applied |
| RastiChat-admin-managed | routing, queues, teams, canned replies, macros, SLA, automations, memberships, hand-added allowed domains | never touched by provisioning |
| Project-admin-managed | launcher, branding, pre-chat form (PR C) | via project configuration; integration may only seed |

## Security model summary

* Browser-supplied ids (`external_id`, `user_id`, `tenant_id`, `role`, project key) are **never** proof of identity.
  Identity comes only from a host-signed assertion, verified server-side.
* Integrations cannot see each other: every tenant lookup is filtered by the authenticated integration
  (cross-integration access returns the same 404 as a missing tenant). Tested.
* Provisioning/identity endpoints: dedicated keys, per-integration rate limit, per-IP failed-auth limit,
  structured refusal logs (no token/claim values), audit rows (identifiers and field names only, no PII).
* Existing guarantees are untouched: WS tickets, visitor-session expiry/revocation/rotation, allowed domains,
  private attachments, URL-credential redaction.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| Redis down | integration tokens refused (503 `replay_store_unavailable`); nothing is accepted unverified |
| Partial provisioning | one DB transaction — all of workspace/project/mapping/audit or none |
| Concurrent first provision | unique constraint resolves the race; the loser retries into the update path (tested) |
| Host retries any call | PUT/DELETE idempotent; mutating non-idempotent calls (PR E) take `Idempotency-Key` |
| RastiChat unavailable | host must treat chat as optional UI (launcher hidden); no host function depends on chat |

## Implementation status

| Slice | PR | Status |
|---|---|---|
| Integration, keys, mapping, signed-request auth, replay protection, scopes, provisioning, lifecycle, audit, throttling, CLI | A | **Implemented** (this PR) |
| Identity assertions, customer/staff/platform bootstrap, identity upgrade, membership removal | B | Specified |
| Launcher config, pre-chat schema/persistence, widget config API, widget + dashboard UI | C | Specified |
| Reference (non-RastiSi) host + generic E2E | D | Specified |
| Platform-initiated conversations | E | Specified |
| RastiSi adapter & UI flows | F, G | Specified |
| Webhook/event delivery | after D | Specified (contract only) |
