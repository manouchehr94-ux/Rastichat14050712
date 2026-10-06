#!/usr/bin/env bash
# Cross-system E2E: a LOCAL RastiSi checkout (with the chat adapter) against a LOCAL RastiChat stack, in real Chromium.
# Everything is synthetic and local (throw-away Postgres databases, *.rastisi.localhost hosts). Never touches a remote host.
#   RASTISI_DIR=/path/to/rastisi E2E_PYTHON=<rastichat venv python> SI_PYTHON=<rastisi venv python> e2e/rastisi/run.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
: "${RASTISI_DIR:?set RASTISI_DIR to a RastiSi checkout that contains apps/chat_integration}"
PY="${E2E_PYTHON:-python}"; SIPY="${SI_PYTHON:-python}"
WORK="$(mktemp -d)"; PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  if [ -n "${E2E_LOG_DIR:-}" ]; then mkdir -p "$E2E_LOG_DIR"; cp "$WORK"/*.log "$E2E_LOG_DIR"/ 2>/dev/null || true; fi
  pkill -f "[n]ext dev -p 3000" 2>/dev/null || true; pkill -f "[n]ext dev -p 3001" 2>/dev/null || true; pkill -f "[n]ext-server" 2>/dev/null || true
  pkill -f "[d]aphne -b 127.0.0.1 -p 8080" 2>/dev/null || true; pkill -f "[h]ttp.server 8081" 2>/dev/null || true
  pkill -f "[m]anage.py runserver 127.0.0.1:8001" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT
DBH="${DB_HOST:-localhost}"; DBU="${DB_USER:-rastichat}"; DBP="${DB_PASSWORD:-rastichat_secret}"
RC_DB="${E2E_DB:-rc_e2e_si}"; SI_DB="${E2E_SI_DB:-rs_e2e}"
for d in "$RC_DB" "$SI_DB"; do
  PGPASSWORD="$DBP" psql -h "$DBH" -U "$DBU" -d postgres -qc "DROP DATABASE IF EXISTS $d" -c "CREATE DATABASE $d" >/dev/null
done

# ---- RastiChat
export DEBUG=1 SECRET_KEY=e2e-only-insecure-key DB_HOST="$DBH" DB_USER="$DBU" DB_PASSWORD="$DBP" DB_NAME="$RC_DB" REDIS_HOST="${REDIS_HOST:-localhost}"
cd "$ROOT/backend"
"$PY" manage.py migrate -v0
"$PY" manage.py shell -c "
from platforms.models import Platform
Platform.objects.get_or_create(external_id='rastisi-e2e', defaults={'name': 'RastiSi (e2e)'})" >/dev/null
"$PY" manage.py integration_create --slug rastisi --name RastiSi --platform-external-id rastisi-e2e \
  --scopes tenants:read,tenants:write,identity:customer,identity:staff,identity:platform,conversations:initiate >/dev/null
"$PY" manage.py integration_keygen --out-dir "$WORK" --name host >/dev/null
KID="$("$PY" manage.py integration_key_add --integration rastisi --public-key-file "$WORK/host.public.pem" | sed -n 's/^kid=//p')"
(cd "$ROOT/packages/widget" && npm run build >/dev/null && python3 -m http.server 8081 --directory dist >/dev/null 2>&1) & PIDS+=($!)
"$PY" -m daphne -b 127.0.0.1 -p 8080 config.asgi:application >"$WORK/daphne.log" 2>&1 & PIDS+=($!)
(cd "$ROOT/apps/operator-dashboard" && npx next dev -p 3000 >"$WORK/dash-operator.log" 2>&1) & PIDS+=($!)
(cd "$ROOT/apps/platform-dashboard" && npx next dev -p 3001 >"$WORK/dash-platform.log" 2>&1) & PIDS+=($!)
for u in http://localhost:8080/api/v1/health/live/ http://localhost:3000/admin/sso http://localhost:3001/platform/sso http://localhost:8081/widget.iife.js; do
  for i in $(seq 1 90); do curl -fsS "$u" >/dev/null 2>&1 && break; sleep 1; done
done

# ---- RastiSi (synthetic data, throw-away DB)
export DJANGO_DEBUG=1 DATABASE_URL="postgres://$DBU:$DBP@$DBH:5432/$SI_DB" DJANGO_SECRET_KEY=e2e-only-insecure-key SI_PORT=8001
export RASTICHAT_INTEGRATION_ENABLED=1 RASTICHAT_BASE_URL=http://localhost:8080 RASTICHAT_WIDGET_URL=http://localhost:8081/widget.iife.js \
  RASTICHAT_DASHBOARD_URL=http://localhost:3000/admin RASTICHAT_PLATFORM_DASHBOARD_URL=http://localhost:3001/platform \
  RASTICHAT_KEY_ID="$KID" RASTICHAT_PRIVATE_KEY_FILE="$WORK/host.private.pem" RASTICHAT_INTEGRATION_SLUG=rastisi \
  RASTICHAT_EXTRA_VERIFIED_DOMAINS="shop-sa.rastisi.localhost:8001,shop-sb.rastisi.localhost:8001,shop-sc.rastisi.localhost:8001"
cd "$RASTISI_DIR"
"$SIPY" manage.py migrate -v0 >/dev/null
SEED_OUT="$WORK/seed.json" "$SIPY" manage.py shell -c "exec(open('$ROOT/e2e/rastisi/seed.py').read())"
"$SIPY" manage.py runserver 127.0.0.1:8001 --noreload >"$WORK/rastisi.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 60); do curl -fsS -o /dev/null -H "Host: shop-sa.rastisi.localhost:8001" http://127.0.0.1:8001/ 2>/dev/null && break; sleep 1; done

cd "$ROOT/e2e"
export SEED_FILE="$WORK/seed.json" SI_PY="$SIPY" SI_DIR="$RASTISI_DIR" RC_PY="$PY" RC_BACKEND="$ROOT/backend"
npx playwright test -c rastisi/playwright.config.ts "$@"
