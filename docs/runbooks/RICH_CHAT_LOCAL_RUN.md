# Rich Chat — Local Run

Two supported paths: Docker Compose (documented in the repo `README.md`) or native services, as used to develop and E2E-test this pass in a sandbox with no Docker daemon available. This runbook documents the native path since it's the one actually verified end-to-end this session.

## 1. Postgres + Redis

```bash
service postgresql start
service redis-server start
sudo -u postgres psql -c "CREATE USER rastichat WITH PASSWORD 'rastichat_secret' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE rastichat_db OWNER rastichat;"
```

## 2. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DB_HOST=localhost DB_PORT=5432 DB_NAME=rastichat_db DB_USER=rastichat DB_PASSWORD=rastichat_secret
export REDIS_HOST=127.0.0.1 SECRET_KEY=dev-secret DEBUG=1 CORS_ALLOW_ALL_ORIGINS=1

python manage.py migrate
python seed_data.py   # creates operator@ws.com / admin@ws.com / support@platform.com, all pass1234, plus a sample project + products

daphne -b 0.0.0.0 -p 8080 config.asgi:application
```

Verify: `curl http://localhost:8080/api/v1/health/` → `{"status":"healthy",...}`.

Note the seeded project's public key from `seed_data.py`'s output (or query it: `python manage.py shell -c "from projects.models import Project; print(Project.objects.get(name='Sample Website').public_key)"`) — needed to point the widget at it.

## 3. Widget

```bash
cd packages/widget
npm ci
npm run build   # produces dist/widget.iife.js

# For manual/dev testing, serve the package directory and open index.html,
# or open e2e.html?projectKey=<the UUID above> for a query-string-configurable harness.
python3 -m http.server 8081 --directory .
```

Open `http://localhost:8081/e2e.html?projectKey=<public_key>` in a browser. The widget's default `apiBase`/`wsBase` (`http://localhost:8080/...`) match the backend started above with no extra config needed; override via `?apiBase=...&wsBase=...` or the `RastiChat.init({apiBase, wsBase})` config for a non-local backend.

## 4. Operator dashboard

```bash
cd apps/operator-dashboard
npm ci
npm run dev   # http://localhost:3000
```

Its `lib/api.ts` defaults (`NEXT_PUBLIC_API_BASE_URL=http://localhost:8080/api/v1`, `NEXT_PUBLIC_WS_BASE_URL=ws://localhost:8080/ws`) also match the backend above with no extra config. Log in at `http://localhost:3000/login` with `operator@ws.com` / `pass1234`.

## 5. Platform dashboard (optional, for support-channel regression only)

```bash
cd apps/platform-dashboard
npm ci
npm run dev   # http://localhost:3001 if 3000 is taken — check next dev output
```

Log in with `support@platform.com` / `pass1234`.

## 6. E2E suite

```bash
cd e2e
npm ci
export E2E_PYTHON=/path/to/backend/.venv/bin/python
export DB_HOST=localhost DB_PORT=5432 DB_NAME=rastichat_db DB_USER=rastichat DB_PASSWORD=rastichat_secret REDIS_HOST=127.0.0.1 SECRET_KEY=dev-secret DEBUG=1
npx playwright test
```

Requires steps 1–4 already running (backend on :8080, widget static server on :8081, operator dashboard on :3000). `global-setup.ts` shells out to `python manage.py shell` to pull the seeded project/product IDs into `.fixture.json` before the suite runs — set `E2E_PYTHON` to the backend venv's Python.

If the sandboxed/pinned Chromium build doesn't match the installed `@playwright/test` version's expected browser build, point `playwright.config.ts`'s `use.launchOptions.executablePath` at the installed browser directly rather than downloading a second copy (see the comment already in that file).
