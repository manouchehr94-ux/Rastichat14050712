# Changelog

All notable changes to RastiChat are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/); versions will be
introduced when the project starts tagging releases. Nothing before the entries below is reconstructed or back-dated.

## [Unreleased] — Standalone integration platform (milestone)

RastiChat becomes a standalone, reusable, multi-tenant messaging platform that other products integrate with through a documented, versioned
contract — without any change to RastiChat itself. This milestone is verified by the full CI suite, a generic reference-host browser E2E in CI, and two
consecutive runs of an isolated staging matrix (PostgreSQL, Redis, nginx with TLS, multiple Daphne workers, real WebSockets, soak, rollback rehearsal).

### Added

* **Integration Contract v1** (`/api/v1/integrations/…`, header `X-RastiChat-Contract: integration-v1`): registered integrations with Ed25519 public
  keys (rotation, instant revocation), scoped least-privilege access, single-use `jti` replay protection (fail-closed on Redis loss), request-bound
  server-to-server tokens, idempotent tenant provisioning with explicit field ownership, suspend/archive/restore, `Idempotency-Key` for
  non-idempotent creations, audit trail, per-integration throttling. Operator CLI: `integration_create`, `integration_key_add`, `integration_key_revoke`,
  `integration_set_active`, `integration_keygen` (demo only), `integration_purge_idempotency`.
* **Trusted identity**: host-signed assertions exchanged for verified customer sessions (`/api/v1/identity/customer/`) and short-lived staff/platform
  dashboard sessions (`/api/v1/identity/staff/`); guest → authenticated upgrade; generic roles `owner|admin|operator`; per-(person, tenant) isolation when the
  host namespaces the staff `sub`; SSO landing page in both dashboards (assertion only in the URL fragment).
* **Deprovisioning**: member/platform-member removal, user and customer disable/enable, tenant suspend/archive — with live WebSocket cut-off and retained history.
* **Configurable launcher and optional pre-chat** (`ProjectWidgetConfig`, `GET /api/v1/widget/config/`): icon-only or icon + label, position, colours, greeting,
  mobile full-screen, RTL/LTR, show/hide rules, guest / signed-in-only policy, and no / one-question / structured pre-chat — pure configuration, validated server-side,
  stored as structured data, visible to operators and automations. Conversations are created lazily (`on_first_message` / `on_open` / `on_load`).
* **Platform ↔ tenant conversations**: tenant admins ↔ platform support, platform-initiated threads (dashboard and `POST …/support-conversations/`), at most one
  active thread per (tenant, subject), close/reopen, notifications, realtime `sender_side`.
* **Host-pushed context** (`PUT|GET|DELETE …/contexts/{customer}/`, scope `context:write`, optional `ctx` claim): flat, ≤ 4 KB, credential-like keys/values refused;
  shown to operators in the conversation sidebar.
* **Observability**: one structured line per security-relevant event and day-bucketed counters (`events_last_24h`) on the token-protected monitoring endpoint.
* **Headless path**: the same public REST + WebSocket protocol the widget uses, documented and proven by `examples/headless/headless.mjs`.
* **Reference host** (`examples/reference-host`, "Acme Learn") and its browser E2E (`e2e/reference-host`, also a CI job), plus a backend generic E2E (`integrations/tests_e2e.py`).
* **Isolated-staging verification harness** (`scripts/staging/sandbox/`) and rollout/rollback runbook.
* **Documentation set**: README, Quickstart, Deployment, Security, Operations, Upgrade, Troubleshooting, API overview, WebSocket protocol, the integration contract and a
  new-project guide, widget embedding and pre-chat configuration.

### Security

* **Database password guard.** In `staging`/`production` the backend now refuses to start if the database password is missing, empty, or the published development value
  (`DB_PASSWORD`, or the password inside `DATABASE_URL`). The development-only default `rastichat_secret` is used only when `ENVIRONMENT=development`.
  **Operator action:** make sure staging/production set a real password before upgrading. Regression-tested (`ProductionLikeDatabasePasswordTests`).

### Changed

* The widget starts conversations lazily by default for provisioned tenants; projects without configuration keep the original `on_load` behaviour.
* The operator support page and platform inbox gained thread lifecycle, "who opened it" and mine-vs-theirs presentation.
* A thread's own messages are no longer counted as unread for their author (support threads only).
* `/api/v1/identity/customer/` is CORS-enabled for projects' allowed domains (it is called by the widget from the host page).

### Fixed (found during verification)

* Browser calls to the customer identity exchange failed under production-like CORS settings.
* The previous release returned HTTP 500 on a migrated database because two new `NOT NULL` columns had no database default; they now carry `db_default`, so code can be
  rolled back without reversing migrations (regression-tested and rehearsed).
* A widget message sent while the initial session/conversation setup was still running could be dropped; it is now queued and delivered in order.

### Migrations

`integrations.0001`–`0004`, `projects.0004_projectwidgetconfig`, `conversations.0007_prechatsubmission`, `conversations.0008_support_threads`,
`notifications.0003_support_threads`. All additive and reversible; see [`docs/UPGRADE.md`](docs/UPGRADE.md).

### New optional environment variables

`INTEGRATION_TOKEN_AUDIENCE`, `INTEGRATION_API_THROTTLE_RATE`, `INTEGRATION_AUTH_FAILURES_PER_MINUTE`, `INTEGRATION_STAFF_SESSION_MINUTES`,
`IDENTITY_EXCHANGE_THROTTLE_RATE`, `WIDGET_CONFIG_THROTTLE_RATE`, `WS_REVALIDATE_INTERVAL_SECONDS` — all with safe defaults
([`docs/runbooks/ENVIRONMENT_VARIABLES.md`](docs/runbooks/ENVIRONMENT_VARIABLES.md)).

### Intentionally deferred (not bugs)

Host-bound webhooks/event delivery (designed, scope reserved), a published TypeScript SDK, a Prometheus exporter, business-hours behaviour, and dashboard languages other
than Persian.
Each has a documented stand-in and an additive path: [`docs/integrations/V1_SCOPE_AND_DEFERRALS.md`](docs/integrations/V1_SCOPE_AND_DEFERRALS.md).

### Security baseline carried into this milestone

Previously delivered and unchanged: strict workspace/platform support isolation; widget identity that never trusts browser-supplied ids; visitor-session
expiry, rotation and revocation; project allowed-domain / Origin enforcement; live WebSocket re-authorisation; single-use WebSocket tickets (no credentials in URLs);
abuse limits; private, per-fetch-authorised attachments; credential-redacting access logs; fail-fast production settings.
