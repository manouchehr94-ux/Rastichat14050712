# Quickstart: run RastiChat locally from zero

Goal: in about 15 minutes have the backend, both dashboards and the widget running on your machine, one integration registered,
and a complete customer ↔ operator conversation proven by the bundled headless script.
Everything here is local and synthetic. **None of the values below are secrets or production values; never reuse them elsewhere.**

## 1. Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.11 (newer 3.x also works) | backend |
| Node.js + npm | 20+ (22+ for the headless script and the reference-host E2E) | widget, dashboards, examples |
| PostgreSQL | 15+ | the only supported database |
| Redis | 7+ | channel layer, WebSocket tickets, replay protection, counters |

PostgreSQL and Redis can be anything reachable. If you have Docker, the repository's dev compose file starts exactly those two:

```bash
docker compose up -d db redis        # PostgreSQL on localhost:5433, Redis on localhost:6380 (dev-only credentials in docker-compose.yml)
```

Without Docker, install both with your package manager and create a database and role of your choice.

## 2. Backend

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# Local development settings (never commit a real .env; see .env.example and docs/runbooks/ENVIRONMENT_VARIABLES.md)
export ENVIRONMENT=development DEBUG=1
export DB_HOST=localhost DB_PORT=5433 DB_NAME=rastichat_db DB_USER=rastichat DB_PASSWORD=rastichat_secret   # matches `docker compose up db`
export REDIS_HOST=localhost REDIS_PORT=6380                                                                 # matches `docker compose up redis`

python manage.py migrate
python -m daphne -b 127.0.0.1 -p 8080 config.asgi:application      # HTTP + WebSocket on http://localhost:8080
```

Check it: `curl -s http://localhost:8080/api/v1/health/ready/` → `{"status": "ready", "components": {…}}` (503 / `not_ready` means the database, Redis or a
migration is missing; `components` says which one).

Optional demo accounts for the dashboards (development only — fixed, well-known passwords):

```bash
python seed_data.py       # operator@ws.com / admin@ws.com / support@platform.com … password "pass1234"
```

Run the backend test-suite when you want confidence (needs the same PostgreSQL and Redis; it creates its own test database):

```bash
python manage.py test
```

## 3. Widget

```bash
cd packages/widget
npm ci && npm run build            # → dist/widget.iife.js   (exposes the global RastiChat)
python3 -m http.server 8081 --directory dist &      # any static server works; production serves it through nginx as /widget.js
```

## 4. Dashboards

The two dashboards are Next.js apps. The API and WebSocket base URLs are **baked in at build/dev start** (`NEXT_PUBLIC_*`).

```bash
export NEXT_PUBLIC_API_BASE_URL=http://localhost:8080/api/v1 NEXT_PUBLIC_WS_BASE_URL=ws://localhost:8080/ws
( cd apps/operator-dashboard && npm ci && npx next dev -p 3000 ) &     # http://localhost:3000/admin   (served under /admin)
( cd apps/platform-dashboard && npm ci && npx next dev -p 3001 ) &     # http://localhost:3001/platform (served under /platform)
```

With the demo accounts: operator dashboard → `operator@ws.com`, platform dashboard → `support@platform.com`.

## 5. Register a first integration (synthetic host)

A *host application* is anything that embeds RastiChat. Registration is an **operator action** (from the backend):

```bash
cd backend
# a platform to attach the integration to (identified by an external id of your choosing)
python manage.py shell -c "
from platforms.models import Platform
Platform.objects.get_or_create(external_id='local-platform', defaults={'name': 'Local Platform'})"

python manage.py integration_create --slug demo-host --name "Demo Host" --platform-external-id local-platform \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,context:write

# Development/demo only: in real life the HOST generates its own keypair and gives you just the PUBLIC key.
mkdir -p /tmp/demo-host && python manage.py integration_keygen --out-dir /tmp/demo-host --name host
python manage.py integration_key_add --integration demo-host --public-key-file /tmp/demo-host/host.public.pem
#   → kid=ick_…   (the host puts this in the JWT header; remember it)
```

## 6. Prove the whole flow (headless)

`examples/headless/headless.mjs` plays a host backend, a customer and an operator against your running backend, using only the
documented public protocol: provisions a tenant, pushes context, exchanges a signed identity assertion, starts a conversation, opens a
WebSocket with a single-use ticket, sends and receives messages, reconnects, resyncs history and checks isolation.

```bash
cd examples/headless
RASTICHAT_URL=http://localhost:8080 INTEGRATION_SLUG=demo-host KEY_ID=ick_… \
PRIVATE_KEY_FILE=/tmp/demo-host/host.private.pem node headless.mjs
#   → "HEADLESS INTEGRATION: ALL OK"
```

## 7. See it in a browser

The reference host ("Acme Learn") is a tiny Node app with its own users and organisations that integrates RastiChat purely through the
contract: three organisations show an icon-only launcher, a one-question prompt and a structured pre-chat form. The one-command version
runs everything against a throw-away database and real Chromium. It starts its **own** backend (8080), widget server (8081) and operator
dashboard (3000) and stops them afterwards, so **stop the ones from steps 2–4 first**:

```bash
# from the repository root; needs PostgreSQL + Redis reachable (the DB_*/REDIS_* variables from step 2; the runner creates and drops its own
# throw-away database `rc_e2e_ref`, so the database role must be allowed to CREATE DATABASE) and Node 22+
( cd packages/widget && npm ci ) && ( cd apps/operator-dashboard && npm ci )
( cd e2e && npm ci && npx playwright install chromium )
export PW_CHROMIUM="$(cd e2e && node -e "console.log(require('@playwright/test').chromium.executablePath())")"
export PGPORT=$DB_PORT                     # the runner's psql calls use PGPORT; REDIS_PORT/DB_PORT are read by the backend
E2E_PYTHON="$(which python)" e2e/reference-host/run.sh
```

To drive the host by hand instead, follow `examples/reference-host/README.md` (it prints a URL such as `http://localhost:4000/o/org-a`).

## 8. Verification cheat-sheet

| Check | Command |
|---|---|
| backend alive / ready | `curl -s localhost:8080/api/v1/health/live/` · `…/health/ready/` |
| migrations in sync | `python manage.py makemigrations --check --dry-run` |
| widget unit tests, types, lint | `cd packages/widget && npm test && npm run typecheck && npm run lint` |
| dashboard tests | `cd apps/operator-dashboard && npm test` (likewise `platform-dashboard`) |
| deployment guardrails | `ENVIRONMENT=production … python manage.py check --deploy --fail-level WARNING --tag security` |
| an integration, end to end | step 6 above |

## Next

* Integrate your own product: [`docs/integrations/NEW_PROJECT_GUIDE.md`](integrations/NEW_PROJECT_GUIDE.md)
* Deploy for real: [`docs/DEPLOYMENT.md`](DEPLOYMENT.md)
