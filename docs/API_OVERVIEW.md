# API overview

A map of the externally usable HTTP APIs, grouped by who calls them and why. It deliberately does **not** repeat request/response
schemas: the normative references are

* **Integration Contract v1** — [`integrations/INTEGRATION_CONTRACT_V1.md`](integrations/INTEGRATION_CONTRACT_V1.md) (everything a host backend calls, and the identity assertions);
* **OpenAPI** — generated from the code: `GET /api/schema/` on a running backend, or
  `cd backend && python manage.py spectacular --file openapi.yml`. (Some legacy generic views produce schema-generation warnings; the contract
  document is authoritative where they differ.)
* **WebSocket protocol** — [`WEBSOCKET_PROTOCOL.md`](WEBSOCKET_PROTOCOL.md).

## Versioning

* All endpoints live under **`/api/v1/`** (WebSockets under `/ws/v2/` — the `v2` is the *ticket-authenticated* generation of the socket
  routes, not a second REST API).
* The host-facing contract is additionally versioned by header: every integration response carries `X-RastiChat-Contract: integration-v1`.
* Evolution is **additive** (new optional fields, new endpoints). Clients must ignore unknown response fields. A breaking change ships as
  `integration-v2` on new paths, with v1 kept for at least 12 months and `Deprecation`/`Sunset` headers on affected responses.
  No endpoint is deprecated in this release.

## Authentication styles (who proves what)

| Caller | Credential | Used for |
|---|---|---|
| host **server** | `Authorization: Bearer <Ed25519-signed JWT>`, aud `<audience>:api`, bound to method+path+body, single use | `/api/v1/integrations/…` |
| browser, relaying a host-signed **assertion** | assertion JWT in the JSON body, aud `<audience>:identity`, single use | `/api/v1/identity/customer/`, `/api/v1/identity/staff/` |
| visitor (customer) | `X-Widget-Session: <session token>` | `/api/v1/widget/…`, attachments |
| staff / operator | `Authorization: Bearer <dashboard JWT>` (password login, or SSO via assertion) | dashboard APIs |
| monitoring | `X-Monitoring-Token: <MONITORING_TOKEN>` | `/api/v1/health/monitoring/` (and error detail on readiness) |
| anonymous | — | health probes, widget config (Origin-checked), public knowledge base |

## 1. Provisioning and management — host backend (`/api/v1/integrations/`)

| Method + path | Scope | Purpose |
|---|---|---|
| `GET /me/` | any | who am I: slug, effective scopes (connectivity check) |
| `PUT\|GET\|DELETE /tenants/{tenant}/` | `tenants:write` / `tenants:read` | idempotent tenant (workspace + default project) provisioning; suspend/restore via `status`; `DELETE` archives |
| `PUT\|DELETE /tenants/{tenant}/members/{user}/` | `identity:staff` | pre-provision / change role / remove a staff member of a tenant |
| `PUT\|DELETE /platform/members/{user}/` | `identity:platform` | platform-level staff (platform owner/admin/support) |
| `POST /users/{user}/disable/` · `…/enable/` | `identity:staff` | disable/re-enable a staff person everywhere |
| `POST /tenants/{tenant}/customers/{user}/disable/` · `…/enable/` | `identity:customer` | cut off / restore one customer |
| `PUT\|GET\|DELETE /tenants/{tenant}/contexts/{customer}/` | `context:write` | push, read, delete the small host-supplied context snapshot of a verified customer |
| `POST /tenants/{tenant}/support-conversations/` | `conversations:initiate` | platform-initiated conversation with a tenant (`Idempotency-Key` required) |

## 2. Identity bootstrap — browser/app, carrying a host assertion (`/api/v1/identity/`)

| Method + path | Purpose |
|---|---|
| `POST /customer/` `{project_key, assertion}` | exchange a customer assertion for a verified visitor session (optionally upgrading the guest's own conversations when `X-Widget-Session` of that guest is presented) |
| `POST /staff/` `{assertion}` | exchange a staff/platform assertion for a short-lived dashboard token (no refresh token) |
| (guest) `POST /widget/init/` `{project_key}` | anonymous visitor session, when the project allows guests |

## 3. Projects and widget configuration

| Method + path | Caller | Purpose |
|---|---|---|
| `GET /widget/config/?project_key=…` | anonymous (Origin-checked) | the versioned launcher / pre-chat / identity-policy document ([`widget/PRE_CHAT_CONFIGURATION.md`](widget/PRE_CHAT_CONFIGURATION.md)) |
| `GET /projects/` | tenant Owner/Admin | projects of workspaces you administer |
| `GET\|PUT /projects/{id}/widget-config/` | Owner/Admin of that workspace | read / replace the stored configuration (strictly validated) |

## 4. Conversations

**Customer side** (`X-Widget-Session`)

| Method + path | Purpose |
|---|---|
| `POST /widget/start/` | start (or resume) the customer's conversation; optional `pre_chat` answers and `create:false` peek |
| `POST /widget/ws-ticket/` | realtime ticket |
| `GET /widget/conversations/{id}/messages/` | history (also the resync after reconnect) |
| `POST /widget/conversations/{id}/upload/` · `mark_read/` · `rate/` · `GET …/branding/` | attachments, read state, rating, consultant branding |
| `POST /widget/session/rotate/` · `revoke/` | rotate the session token / log out |

**Staff side** (dashboard JWT) — the full inbox: `GET\|POST /conversations/customer/…` (list, assign, transfer, priority, status, upload,
tags, notes, `customer-context/` incl. pre-chat answers, verified-identity badge and host context, SLA), `GET /conversations/{id}/messages/`,
`POST /conversations/{id}/send/`, teams, queues, SLA policies, quick replies, macros, automations, notifications, knowledge base
(`/kb/…`; public read-only subset under `/kb/public/`). The OpenAPI document lists them all.

**Platform ↔ tenant support threads** (dashboard JWT)

| Method + path | Caller | Purpose |
|---|---|---|
| `POST /support/start/` · `POST /support/` · `GET /support/` | tenant Owner/Admin | open or resume a thread with the platform / list threads |
| `POST /platform/support/start/` · `GET /platform/support/workspaces/` | platform Owner/Admin | start a thread with a tenant that never wrote first / the tenant picker |
| `GET\|POST /platform/support/…` · `…/{id}/close/` · `…/reopen/` | platform staff | the platform inbox |
| `POST /support/{id}/close/` · `…/reopen/` | tenant Owner/Admin | lifecycle from the tenant side |

## 5. Attachments

`GET /attachments/{message}/?sig=…` is the only way to fetch a chat attachment (re-authorised on every fetch; nginx streams it).
URLs expire after 10 minutes: refresh with `GET /attachments/{message}/refresh/` (staff JWT or session) or
`GET /widget/attachments/{message}/refresh/`. See [`runbooks/PRIVATE_ATTACHMENTS.md`](runbooks/PRIVATE_ATTACHMENTS.md).

## 6. Realtime

`POST /ws/ticket/` (staff) and `POST /widget/ws-ticket/` (customer) mint tickets; sockets under `/ws/v2/…`. See [`WEBSOCKET_PROTOCOL.md`](WEBSOCKET_PROTOCOL.md).

## 7. Monitoring and health

| Path | Auth | Purpose |
|---|---|---|
| `GET /health/live/` | none | liveness |
| `GET /health/ready/` | none (error text needs the token) | readiness: database, Redis, migrations |
| `GET /health/monitoring/` | `X-Monitoring-Token` | scheduler heartbeats, disk, backup freshness, `events_last_24h` counters |

## Errors

Integration and identity endpoints use `{"error": {"code", "message", "details"?, "request_id"}}` with stable `code` values (Contract §4).
Older widget/dashboard endpoints keep their existing `{"error": "…", "code": "…"}` shapes for backward compatibility. Authorisation failures
for resources the caller must not know about are a uniform `404`. Rate limits answer `429` with `Retry-After`.

## Not available in v1

Outbound **webhooks/events**, a published **SDK**, and a **Prometheus** endpoint are deliberately deferred; see
[`integrations/V1_SCOPE_AND_DEFERRALS.md`](integrations/V1_SCOPE_AND_DEFERRALS.md) for what to use instead and how they would be added.
