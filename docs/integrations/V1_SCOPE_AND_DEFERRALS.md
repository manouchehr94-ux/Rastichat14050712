# Integration Contract v1 — what exists, what is deferred, and why

Audience: a developer deciding whether RastiChat can carry their product **today**, and a maintainer planning v1.x.
Legend: **DONE** (implemented + tested) · **DEFERRED** (not built; safe to defer for the reason given; additive later) ·
**PARTIAL** (usable, with the stated limit) · **N/A** (depends on infrastructure RastiChat does not have).
Section numbers refer to the original master implementation specification (an internal planning document that is not part of this repository); every row names the capability, so the table reads on its own.

## Reusability proof
An unrelated product integrates using only: Integration Contract v1
(`INTEGRATION_CONTRACT_V1.md`), the widget (`docs/widget/EMBEDDING.md`) or the headless path (`HEADLESS.md`), and configuration.
Two automated proofs run on every CI build:
1. `examples/reference-host` ("Acme Learn": own users/orgs/login, ~150 lines) — provisioning, icon-only launcher, no/one/structured
   pre-chat, guest + trusted customer, staff SSO, operator reply, realtime reply, in real Chromium (`e2e/reference-host/`).
2. `examples/headless/headless.mjs` — the same flow with **no widget and no RastiChat code**, only the documented REST/WebSocket protocol.
Neither adds any code to RastiChat: both use a registered integration + keys (`integration_create`, `integration_key_add`).
A real multi-tenant commerce platform has also integrated through the same contract and gets no privileged path (optional case study: `docs/case-studies/RASTISI.md`).

## Capability matrix

| Spec § | Capability | Status | Evidence / note |
|---|---|---|---|
| 3, 27 | Minimal sender (icon only), no second login | DONE | widget launcher; reference-host E2E Mode A |
| 4 | Configurable launcher (mode, position, offsets, label, tooltip, colour, icon, badge, greeting, auto-open, mobile, RTL/LTR, language, hide/show rules, guest / authenticated-only, capability flags) | DONE | `projects/widget_config.py`, `docs/widget/EMBEDDING.md` |
| 4 | Business-hours behaviour | N/A | spec: "if existing infrastructure supports it" — RastiChat has no business-hours model; adding one is a separate product feature |
| 5 | Pre-chat: none / one question / structured form by configuration; all listed field types; server-side validation; persisted generically; visible to operator and to automations | DONE | `PreChatSubmission`, `docs/widget/PRE_CHAT_CONFIGURATION.md` |
| 5 | Pre-chat answers in **routing** | PARTIAL | answers are stored *before* routing/automation run and automation conditions can read them (`conversation.pre_chat[…]`) so an automation can route; the built-in queue-routing rules do not yet take pre-chat as an input |
| 5, 19 | Pre-chat answers in **webhooks/events** | DEFERRED | follows webhooks (below) |
| 6 | Receiver keeps the full engine (inbox, assignment, queues, SLA, macros, notes, tags, transfer, automations…) | DONE | integrations feed the existing conversation engine; no second backend |
| 7 | Directions A (customer↔tenant staff), B (tenant admin↔platform), C (platform→tenant, platform-initiated) | DONE | `support_service`, Contract §12; tenant→customer / system-created conversations are not blocked by the model but have no API yet |
| 8, 9, 20 | Integration, tenant mapping, external identity; idempotent provisioning; ownership rules | DONE | Contract §6, `INTEGRATION_PLATFORM.md` |
| 10 | Versioned contract usable without reading any host's source | DONE | `INTEGRATION_CONTRACT_V1.md`, `NEW_PROJECT_GUIDE.md` |
| 11–13 | Signed assertions (Ed25519, kid rotation, short TTL, jti replay protection), guest/trusted/hybrid customer, staff SSO | DONE | Contract §3, §7; negative tests (expired, replayed, wrong audience/issuer/integration, forged tenant) |
| 14 | Deprovisioning/revocation (user, membership, tenant, project, integration, key, archive) with live WebSocket cut-off, history retained | DONE | Contract §8 |
| 15 | Widget/embed API (`RastiChat.init({project, bootstrap})`, reconnect, history resync, session renewal, remote config, graceful offline, RTL, mobile) | DONE | `packages/widget`, 68 unit tests |
| 16 | Headless path | DONE (docs + executable proof) / SDK DEFERRED | `HEADLESS.md`, `examples/headless`. A TypeScript client package is *optional* in the spec ("if useful… do not create an unnecessary giant SDK"). Safe to defer: every call is versioned HTTP/WS and the protocol is proven by a runnable zero-dependency script; a wrapper is purely additive and removes no host-side step |
| 16 | OpenAPI/schema | PARTIAL | `/api/schema/` is generated and includes the integration/identity endpoints, with the generic-view warnings that already existed on the legacy endpoints; the contract document is the authoritative reference |
| 17 | Widget configuration API (versioned, validated, backward compatible) | DONE | `GET /api/v1/widget/config/` |
| 18 | Host-pushed, tenant-scoped context; operator sees it; no host DB access | DONE | `PUT|GET|DELETE …/contexts/{customer}/` (scope `context:write`) and optional `ctx` claim in the customer assertion; flat, ≤4 KB, no credential-looking keys/values, no "sensitive" section; shown in the operator sidebar |
| 19 | Webhooks / events | **DEFERRED (explicitly allowed by the spec)** | See below |
| 21–26 | First real host adapter (mapping, off-by-default flag, three flows) | DONE | lives in the host's own repository; optional case study `docs/case-studies/RASTISI.md` |
| 28–29, 40 | Reference integration, reusability acceptance, generic E2E | DONE | see "Reusability proof" |
| 30 | API versioning / deprecation policy | DONE | Contract §2: `/api/v1`, additive evolution, `Deprecation`/`Sunset` headers. **Deprecated endpoints in this release: none**; no existing endpoint, field or message shape was removed or changed (new fields/endpoints are additive; legacy widget start mode `on_load` remains the default for projects not provisioned through the contract) |
| 31, 32 | Security invariants (tenant isolation, WS tickets, visitor sessions, attachments, domains, logging, rate limits, per-integration least privilege) | DONE | preserved + negative tests; staging log review |
| 33 | Privacy / data minimisation, classification, retention | DONE | `INTEGRATION_PLATFORM.md` → "Data classification and retention" |
| 34 | Audit of critical operations | DONE | `integrations/audit.py` writes to the existing audit table (key names/identifiers only, never payload values) |
| 35 | Reliability (retries, Redis/RastiChat/host unavailable, partial provisioning) | DONE | `INTEGRATION_PLATFORM.md` → "Failure behaviour" |
| 36 | Metrics / structured logging | DONE (logs + counters) | `common/observability.py`: one structured line per event and day-bucketed Redis counters on `/api/v1/health/monitoring/` (`events_last_24h`). No Prometheus endpoint (see below) |
| 37 | Migration discipline (fresh DB, upgrade from current main, synthetic existing rows) | DONE | additive migrations only; checked in the staging matrix |
| 38 | UI (Persian/RTL, desktop/mobile, keyboard, loading/error/offline states) | DONE | |
| 39 | Tests incl. negative-first security tests | DONE | |
| 41 | Host-adapter E2E (three directions, two stores, multi-store admin, operator without admin rights, wrong store, disabled flag, revoked membership, reconnect, authenticated customer, guest) | DONE | executed against the first real host adapter in the maintainers' isolated staging matrix (needs that host's checkout, so it is not part of the generic CI or this distribution); the generic equivalent runs in CI: `e2e/reference-host/` |
| 42, 43 | Documentation set and the "integrate a new application" guide | DONE | `docs/` index in the repository README; `NEW_PROJECT_GUIDE.md` |
| 46–49 | Combined verification and isolated staging (two consecutive full matrix runs, soak, rollback rehearsal) | DONE | recorded in the release verification report; not reproduced by CI alone |

## Webhooks / events — deferred, and why that is safe
The specification (§19) says, in substance: *if webhooks are not required for the first host's slice, design the contract and implement only
what is necessary, but do not create a host-specific event mechanism.*
* **Not required for the first slice.** All three conversation directions are request/response + realtime inside RastiChat. The first host
  never needs RastiChat to call it back: identity, tenant, membership and context all flow host → RastiChat.
* **The contract is designed** (Contract §13: signed Ed25519 deliveries, `event_id`, `version`, tenant + integration identity,
  at-least-once with bounded backoff, minimal PII, SSRF-hardened callback URLs) and the scope `events:receive` is reserved, so
  adding delivery later changes nothing a current integration relies on.
* **Why not now:** an outbound HTTP sender is the one feature that makes RastiChat *call arbitrary URLs*. Doing it correctly
  (https-only, resolved-IP checks against private/link-local/metadata ranges **at connect time** to defeat DNS rebinding, no
  redirects, timeouts, retry queue with failure visibility, consumer idempotency) is a security-sensitive slice that deserves its own
  review, not a late add-on to this stack.
* **What a host cannot do in v1:** react to "conversation closed / assigned / rated" without a person looking at the operator UI.
  Hosts that need that should wait for the events slice. Nothing in v1 blocks it and no host-specific mechanism exists to migrate away from.

## Metrics — what exists
`common/observability.emit(event, label=…, **fields)` is called at: token refusals (by code, incl. replay), scope denials,
API errors, cross-tenant denials, rate limits, identity exchanges (customer/staff), every audit event (provisioning,
status changes, role mapping, identity link/revoke, guest upgrade, context update), widget config served, pre-chat submissions,
customer conversations created, support threads started/resumed, WebSocket ticket issue and WebSocket authentication outcome
(success/refused = connect/reconnect). Field names that sound like credentials are dropped; values are sanitised and truncated.
A Prometheus exporter is not included: the counters are a few lines to export once a metrics stack exists (the repository has none).

## Deferred capabilities — status, what to use today, and how they would be added

| Capability | Status in v1 | Use today | Can it be added later without breaking integrations? |
|---|---|---|---|
| **Host-bound webhooks / event delivery** | not implemented (contract designed, scope `events:receive` reserved) | no host-facing polling API exists; hosts that need to react to "closed/assigned/rated" cannot do so without a person using the operator UI. Everything a host *sends* (provisioning, identity, context, support threads) works today | **Yes.** The contract (§13) is fixed: signed Ed25519 deliveries, `event_id`, at-least-once, SSRF-hardened URLs. It is a new outbound sender plus a registration endpoint; no existing request or response changes |
| **Published TypeScript (or other) SDK** | not shipped (optional in the spec) | the raw versioned REST/WebSocket contract; `examples/reference-host/lib/rastichat.mjs` is a 60-line host-side signer, `examples/headless/headless.mjs` a complete client flow; OpenAPI at `/api/schema/` for generators | **Yes.** A package would only wrap the same calls; it removes no host-side step (signing assertions must stay on the host's server) |
| **Prometheus exporter** | not shipped | structured one-line events (`rastichat_event event=… label=…`) and day-bucketed counters at `GET /api/v1/health/monitoring/` (`events_last_24h`), plus health endpoints | **Yes.** The counters live in Redis and could be exposed in Prometheus format by a small, token-protected view |
| **Business-hours behaviour** (offline form / "we're closed" launcher) | not applicable: RastiChat has no business-hours model for the sender UI (operator-side SLA calendars exist) | hide the launcher with `visibility` path rules, use `launcher.enabled`, or let the host decide client-side | **Yes**, as a new optional field of the versioned widget-configuration document |
| **Pre-chat answers as a routing input** | partial | automation rules can read `conversation.pre_chat[<key>]` (set queue/team/priority) | **Yes**, additive |
| **Complete OpenAPI for every legacy endpoint** | partial | the contract document is authoritative; `/api/schema/` includes the integration, identity and widget endpoints | **Yes**, by annotating legacy views |
| **Dashboard languages other than Persian** | the operator and platform dashboards are Persian/RTL only (no i18n layer); the widget supports `fa` and `en` | the widget in English or Persian; host-side staff UIs are unaffected | **Yes**, by introducing a translation layer in the two Next.js apps; no API change |
| **Additional channels** (email, messaging apps) | out of scope | — | not designed in v1; the conversation engine is channel-agnostic, a channel would be a new adapter |
