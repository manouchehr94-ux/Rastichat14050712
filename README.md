# RastiChat

RastiChat is a **standalone, multi-tenant messaging platform**: a small chat icon on any website or app, a full-featured
inbox for the people who answer, and a platform-level support channel between the operator of the platform and each of its tenants.

It is built to be **embedded in other products**. A host application (an e-commerce platform, a SaaS app, a learning portal…) connects
through a documented, versioned contract: it provisions its customers' organisations as tenants, tells RastiChat who is signed in using
short-lived signed assertions, and embeds one script tag, or builds its own UI against the same public API. **No change to RastiChat
itself is needed to integrate a new product.**

> Status: the integration platform (contract v1, trusted identity, configurable launcher and pre-chat, platform↔tenant
> conversations, host context, observability) is complete and verified. See [`CHANGELOG.md`](CHANGELOG.md) and the list of
> intentionally deferred capabilities in [`docs/integrations/V1_SCOPE_AND_DEFERRALS.md`](docs/integrations/V1_SCOPE_AND_DEFERRALS.md).

## What it does

| Role | Experience |
|---|---|
| **Sender** (a customer on the host's site) | A small message icon. Click it: the chat opens directly, or asks **one** question, or shows a short structured form — pure configuration. Signed-in customers never see a second login; guests can chat when the tenant allows it. |
| **Receiver** (the tenant's staff) | A complete inbox: assignment, queues, teams, SLA, canned replies, macros, notes, tags, automations, knowledge base, attachments, voice notes, real-time everything. Staff reach it by SSO from the host — no second password. |
| **Platform operator** | A platform dashboard and a support channel in both directions: tenant admins can ask the platform for help, and the platform can start a conversation with a tenant that never wrote first. |
| **Host application** | Server-to-server provisioning and identity (Ed25519-signed JWTs), optional context push, widget or headless integration. |

**Localisation.** The widget ships Persian and English strings with RTL/LTR layout (set per project). The operator and platform dashboards are
currently **Persian (RTL) only** — there is no translation layer yet; see the deferred list.

## Concepts

* **Platform** – the operator of the RastiChat deployment (and of the products that integrate with it).
* **Integration** – one registered host application; owns public keys and a list of permitted scopes.
* **Tenant** – one customer organisation of the host. In RastiChat: a *workspace* with a default *project* (the widget deployment),
  reachable only through an explicit `IntegrationTenantMapping`.
* **Visitor / customer** – a guest, or a person the host has vouched for with a signed assertion.
* **Staff** – workspace owners, admins and operators, and platform staff; provisioned from the host's own roles.

Core chat data carries no host-specific columns. The mapping table is the only place an external id lives.

## Architecture at a glance

```
 host backend ── Ed25519-signed JWT ──► /api/v1/integrations/…   (provisioning, members, context, support threads)
 host backend ── signed assertion ────► browser ──► /api/v1/identity/{customer,staff}/ ──► session
 browser: widget.js (or your own UI) ⇄ REST /api/v1/widget/…  and  WebSocket /ws/v2/…  (single-use tickets)
 operator & platform dashboards (Next.js)  ⇄  the same REST + WebSocket API
 nginx ─► Daphne workers (Django + Channels) ─► PostgreSQL   +   Redis (channel layer, tickets, replay protection, counters)
```

* Backend: Django 5.2, Django REST Framework, Channels/Daphne, PostgreSQL, Redis. (`backend/`)
* Widget: dependency-free TypeScript bundle built with Vite. (`packages/widget/`)
* Dashboards: Next.js, TypeScript, Tailwind (`apps/operator-dashboard`, `apps/platform-dashboard`).
* Deployment templates: nginx sites/snippets, Dockerfiles, compose files. (`deploy/`, `docker/`)
* Examples and proofs: a generic reference host, a headless client, generic end-to-end tests. (`examples/`, `e2e/`)

## Requirements

Python 3.11 (what CI and the Docker images use; 3.12 is expected to work; **not 3.13** until the pinned `psycopg2-binary==2.9.9` is bumped), Node.js 20+ (22+ for the reference-host E2E and the headless example), PostgreSQL 15+, Redis 7+.
PostgreSQL is the only supported database (there is no SQLite mode).

## Quick start

The full, copy-pasteable walkthrough is [`docs/QUICKSTART.md`](docs/QUICKSTART.md). In short:

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export ENVIRONMENT=development DEBUG=1 DB_HOST=localhost DB_NAME=rastichat DB_USER=rastichat DB_PASSWORD=<local dev password> REDIS_HOST=localhost
python manage.py migrate
python -m daphne -b 127.0.0.1 -p 8080 config.asgi:application
```

Then build the widget and dashboards and register a first integration — see the Quickstart. To see an *entire* integration work end to end
in a real browser against a throw-away local stack, run the generic reference-host E2E: `e2e/reference-host/run.sh`.

## Documentation

| I want to… | Read |
|---|---|
| run it locally | [`docs/QUICKSTART.md`](docs/QUICKSTART.md) |
| integrate my product | [`docs/integrations/NEW_PROJECT_GUIDE.md`](docs/integrations/NEW_PROJECT_GUIDE.md), then the normative [`INTEGRATION_CONTRACT_V1.md`](docs/integrations/INTEGRATION_CONTRACT_V1.md) |
| embed the chat icon | [`docs/widget/EMBEDDING.md`](docs/widget/EMBEDDING.md), [`PRE_CHAT_CONFIGURATION.md`](docs/widget/PRE_CHAT_CONFIGURATION.md) |
| build my own chat UI | [`docs/integrations/HEADLESS.md`](docs/integrations/HEADLESS.md), [`docs/WEBSOCKET_PROTOCOL.md`](docs/WEBSOCKET_PROTOCOL.md), [`docs/API_OVERVIEW.md`](docs/API_OVERVIEW.md) |
| deploy it | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md), [`docs/runbooks/ENVIRONMENT_VARIABLES.md`](docs/runbooks/ENVIRONMENT_VARIABLES.md) |
| operate and monitor it | [`docs/OPERATIONS.md`](docs/OPERATIONS.md), [`docs/runbooks/MONITORING_RUNBOOK.md`](docs/runbooks/MONITORING_RUNBOOK.md) |
| upgrade or roll back | [`docs/UPGRADE.md`](docs/UPGRADE.md), [`docs/runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md`](docs/runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md) |
| understand the security model | [`docs/SECURITY.md`](docs/SECURITY.md) |
| fix a problem | [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) |
| see what is *not* built yet | [`docs/integrations/V1_SCOPE_AND_DEFERRALS.md`](docs/integrations/V1_SCOPE_AND_DEFERRALS.md) |
| understand the design | [`docs/architecture/INTEGRATION_PLATFORM.md`](docs/architecture/INTEGRATION_PLATFORM.md) |

An integration of a real product (an e-commerce platform) is described as an optional case study in
[`docs/case-studies/RASTISI.md`](docs/case-studies/RASTISI.md). It is an *example host*; RastiChat has no dependency on it.

## Tests

```bash
cd backend && python manage.py test                 # backend (PostgreSQL + Redis required)
cd packages/widget && npm ci && npm test            # widget
cd apps/operator-dashboard && npm ci && npm test    # operator dashboard (likewise platform-dashboard)
e2e/reference-host/run.sh                           # generic host, real Chromium (needs Postgres + Redis)
```

CI (`.github/workflows/pr-checks.yml`) runs the backend suite, nginx config validation, widget and dashboard checks, Docker builds,
secret scanning and the reference-host E2E on every pull request.

## Security

See [`docs/SECURITY.md`](docs/SECURITY.md) for the model and how to report a vulnerability. Never commit real `.env` files, keys or dumps;
only the `.env*.example` templates are tracked. Staging and production refuse to start without an explicit database password.

## License

RastiChat is licensed under the [Apache License, Version 2.0](LICENSE). Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS, without warranties or conditions of any kind.
