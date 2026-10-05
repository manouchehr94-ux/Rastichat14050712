# Django 5.2 — isolated staging test plan

**Status: plan and tooling only.** Nothing in this document or in the referenced tools touches the live chat server,
its database, Nginx, remotes, or RastiSi. Django 5.2 (with DRF 3.17, Channels 4.3, simplejwt, drf-spectacular 0.30) plus the P1
hardening (WS revocation, visitor-session lifecycle, WS tickets, allowed domains, abuse protection) must pass this plan
on an **independent staging stack with synthetic data** before any deployment is even proposed.

## 1. Isolation rules (all mandatory)

| Rule | How it is enforced |
|---|---|
| Separate host/VM/containers, never the live VPS | Operator provisions it; tools refuse forbidden hosts (`chatchat.rastisi.ir`, `rastisi.ir`, `www.`/`app.` + `DJANGO52_FORBIDDEN_HOSTS`) |
| Own PostgreSQL and Redis, empty, created for the test | `scripts/staging/django52-preflight.sh` requires an explicit `DB_HOST` |
| Synthetic data only (no customer export, no production dump) | §3 seed list; never restore a backup into staging |
| Own secrets (`SECRET_KEY`, JWT, DB password) generated for staging | never reuse production values |
| Explicit opt-in | `DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack` required by preflight and Playwright |
| No outbound side effects | email backend = console/file, no payment/SMS keys, no RastiSi URLs configured |
| No deploy workflow is run for this | the stack is built by hand/compose from a PR branch |

## 2. Stack to build

* Branch under test: the combined P1 integration branch (or `main` + P1 PRs), built with `docker compose` from
  `docker-compose.staging.yml` style files on a separate machine, behind its own Nginx/Caddy that **does** proxy WebSocket
  upgrades (the thing local runs cannot prove).
* `DEBUG=0`, `ENVIRONMENT=staging` (so `IS_PRODUCTION_LIKE` guardrails are active), TLS on a staging hostname such as
  `chat-stg.example.test`, `ALLOWED_HOSTS`/`CORS`/`CSRF` set for that hostname only.
* `LEGACY_URL_CREDENTIALS_ENABLED` **unset** (the secure default must hold); then one extra pass with
  `LEGACY_URL_CREDENTIALS_ACK=accept-credentials-in-urls` to prove the guardrail warning (`common.W002`) fires.
* Daphne (ASGI) for HTTP+WS, one worker first, then 2 workers behind the proxy to prove channel-layer fan-out via Redis.

## 3. Synthetic seed (create with a throw-away management script or the admin; never commit credentials)

1. Platform + 2 workspaces (`ws-a`, `ws-b`), each with one active project (`public_key` recorded in the env file).
2. Users: `owner-a`, `agent-a1`, `agent-a2` (workspace A), `agent-b1` (B), `platform-super` (superuser), one **inactive** user.
3. Project A `allowed_domains` = `embed-allowed.example.test` ; project B left empty.
4. A few conversations + messages with Persian text (RTL, ZWNJ, emoji, long words, links).

## 4. Environment for the Playwright suite

```
export DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack
export SMOKE_BACKEND_URL=https://chat-stg.example.test
export SMOKE_WS_URL=wss://chat-stg.example.test
export SMOKE_OPERATOR_URL=https://operator-stg.example.test
export SMOKE_PLATFORM_URL=https://platform-stg.example.test
export SMOKE_WIDGET_URL=https://chat-stg.example.test/widget/rastichat-widget.js
export SMOKE_PROJECT_KEY=<project A public key>
export SMOKE_OPERATOR_EMAIL=agent-a1@example.test
export SMOKE_OPERATOR_PASSWORD=<staging-only password>
export DJANGO52_ALLOWED_EMBED_ORIGIN=https://embed-allowed.example.test
export DJANGO52_FORBIDDEN_EMBED_ORIGIN=https://embed-forbidden.example.test
export DJANGO52_EXPECT_LEGACY_URL_OFF=1
cd e2e && npm ci && npx playwright install chromium   # on a machine that is allowed to download browsers
npm run typecheck:django52 && npm run test:django52
```

Optional engines: `DJANGO52_EXTRA_BROWSERS=1` adds Firefox and WebKit projects.

## 5. Test matrix

| # | Area | Where | What must hold |
|---|---|---|---|
| 0 | Preflight (read-only) | `scripts/staging/django52-preflight.sh` | Django is 5.2, `check --deploy` clean, no migration drift, `pip-audit` clean |
| 1 | Migrations on empty DB, then on a DB seeded by the *old* release | staging DB only | `migrate` succeeds both ways; legacy `VisitorSession` rows get `expires_at`/`hard_expires_at`; no conversation deleted |
| 2 | Unit/integration suite | CI + staging venv | full `manage.py test` green on PostgreSQL 15/16 |
| 3 | Real WebSocket through the proxy | `01-ws-tickets` | no credential in any socket URL; ticket as first frame; replay refused; legacy URL access refused |
| 4 | Reconnect / network loss | `02-ws-reconnect` | offline banner, reconnect with a **fresh** ticket, two tabs both receive replies |
| 5 | Domains / CORS / Origin | `04-domains-cors` | allowed origin works; forbidden origin gets 403 `origin_not_allowed` and no socket; dashboard endpoints never get CORS for embed origins |
| 6 | Session lifecycle in a browser | `05-session-lifecycle` | logout revokes server-side; revoked session recovers once into a clean guest session |
| 7 | RTL + responsive | `03-rtl-mobile` on desktop, Pixel 7, iPhone 13 emulation | `dir=rtl`, no horizontal scroll, panel inside viewport, controls usable |
| 8 | API smoke on Django 5.2 | `06-api-django52-smoke` | health, JWT login, security headers, admin + OpenAPI render, P0 regressions (no generic PATCH/DELETE) |
| 9 | Revocation while connected | manual + scripted | deactivate a user / remove membership while the dashboard socket is open → socket closes with 4403 within `WS_REVALIDATE_SECONDS` and receives nothing further |
| 10 | Multi-worker fan-out | 2 Daphne workers | message sent via worker 1 reaches a socket held by worker 2; presence updates correct |
| 11 | Proxy behaviour | Nginx/Caddy | `Upgrade` headers, idle timeout ≥ heartbeat, `X-Forwarded-*`, request-id header, large-body limits |
| 12 | Real device pass | one iOS Safari and one Android Chrome by hand | background/foreground reconnect, keyboard overlap, Persian input, widget launcher placement |
| 13 | Soak | 30 min, ~50 synthetic sockets | no memory growth in Daphne, Redis keys `wsticket:*` expire, no event-loop errors in logs |
| 14 | Rollback rehearsal | staging only | restore the staging DB snapshot and previous image; chat comes back; documents timings |

Exit criteria: every row green on two consecutive runs, zero ERROR-level log lines attributable to Channels/asyncio
during rows 3–13, and a written sign-off in the PR. Only then may a deployment be proposed — separately, with its own
approval and the rollout order in `WS_TICKETS_AND_URL_CREDENTIALS.md` (backend with legacy+ack → clients → legacy off).

## 6. Known gaps this plan is meant to close

* The Django 5.2 stack has only been exercised by the test client and in-process Channels communicators, not by a browser
  through a reverse proxy.
* An intermittent asyncio event-loop error was seen once under four parallel full suites; its root cause is unproven.
  Row 13 (soak) and the logs of rows 3–10 are the evidence to collect.
* Attachments are still public capability URLs (tracked as P2); not covered here.
