#!/usr/bin/env bash
# Runs the reference-host E2E (a NON-RastiSi host: provision -> launcher/pre-chat modes -> guest/trusted customer -> staff SSO -> realtime reply).
#   STACK=local   (default) starts a LOCAL, synthetic stack (Postgres + Redis must already be running), runs the suite, tears down.
#   STACK=staging runs the SAME suite against the isolated SANDBOX staging stack (nginx TLS -> balancer -> 2 Daphne workers, Next
#                 standalone dashboards, widget from nginx): needs SANDBOX_DIR (see scripts/staging/sandbox/README.md). It registers a
#                 throw-away integration on the sandbox database and leaves the stack running.
# Never touches any remote host. Usage: e2e/reference-host/run.sh   (needs: backend venv python in $E2E_PYTHON)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${E2E_PYTHON:-python}"
STACK="${STACK:-local}"
WORK="$(mktemp -d)"; PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  if [ -n "${E2E_LOG_DIR:-}" ]; then mkdir -p "$E2E_LOG_DIR"; cp "$WORK"/*.log "$E2E_LOG_DIR"/ 2>/dev/null || true; fi
  pkill -f "[n]ode server.mjs" 2>/dev/null || true
  if [ "$STACK" = local ]; then
    # npx/npm wrappers leave their children behind: stop the servers by what they run
    pkill -f "[n]ext dev -p 3000" 2>/dev/null || true; pkill -f "[n]ext-server" 2>/dev/null || true
    pkill -f "[d]aphne -b 127.0.0.1 -p 8080" 2>/dev/null || true; pkill -f "[h]ttp.server 8081" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

if [ "$STACK" = staging ]; then
  ST="${SANDBOX_DIR:?STACK=staging needs SANDBOX_DIR}/stg"
  SLUG="acme-learn-$(date +%s)"
  ( set +u; . "$ST/backend.env"; cd "$ROOT/backend"
    PLAT="$("$PY" manage.py shell -c "from platforms.models import Platform; print(Platform.objects.get(name='STG Platform').id)" 2>/dev/null | tail -1)"
    "$PY" manage.py integration_create --slug "$SLUG" --name "Acme Learn (staging e2e)" --platform-id "$PLAT" \
      --scopes tenants:read,tenants:write,identity:customer,identity:staff,context:write >/dev/null 2>&1
    "$PY" manage.py integration_keygen --out-dir "$WORK" --name host >/dev/null 2>&1
    "$PY" manage.py integration_key_add --integration "$SLUG" --public-key-file "$WORK/host.public.pem" 2>/dev/null | sed -n 's/^kid=//p' > "$WORK/kid" )
  KID="$(cat "$WORK/kid")"; [ -n "$KID" ] || { echo "could not register the staging integration"; exit 1; }
  RC_URL=https://chat-stg.example.test; DASH_URL=https://operator-stg.example.test/admin
  WIDGET_SRC=https://chat-stg.example.test/widget.js
  export OPERATOR_HOST=operator-stg.example.test NODE_EXTRA_CA_CERTS="$ST/ca.crt" NO_PROXY='*' no_proxy='*'
  unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy || true
else
  SLUG=acme-learn
  export DEBUG=1 SECRET_KEY=e2e-only-insecure-key DB_HOST="${DB_HOST:-localhost}" DB_USER="${DB_USER:-rastichat}" DB_PASSWORD="${DB_PASSWORD:-rastichat_secret}" DB_NAME="${E2E_DB:-rc_e2e_ref}" REDIS_HOST="${REDIS_HOST:-localhost}"
  cd "$ROOT/backend"
  PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d postgres -qc "DROP DATABASE IF EXISTS $DB_NAME" -c "CREATE DATABASE $DB_NAME" >/dev/null
  "$PY" manage.py migrate -v0
  "$PY" manage.py shell -c "
from platforms.models import Platform
Platform.objects.get_or_create(external_id='e2e-platform', defaults={'name': 'E2E Platform'})" >/dev/null
  "$PY" manage.py integration_create --slug acme-learn --name "Acme Learn" --platform-external-id e2e-platform \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,context:write >/dev/null
  "$PY" manage.py integration_keygen --out-dir "$WORK" --name host >/dev/null
  KID="$("$PY" manage.py integration_key_add --integration acme-learn --public-key-file "$WORK/host.public.pem" | sed -n 's/^kid=//p')"

  (cd "$ROOT/packages/widget" && npm run build >/dev/null && python3 -m http.server 8081 --directory dist >/dev/null 2>&1) & PIDS+=($!)
  "$PY" -m daphne -b 127.0.0.1 -p 8080 config.asgi:application >"$WORK/daphne.log" 2>&1 & PIDS+=($!)
  (cd "$ROOT/apps/operator-dashboard" && npx next dev -p 3000 >"$WORK/dash.log" 2>&1) & PIDS+=($!)
  for u in http://localhost:8080/api/v1/health/live/ http://localhost:3000/admin/login http://localhost:3000/admin/sso http://localhost:8081/widget.iife.js; do
    for i in $(seq 1 60); do curl -fsS "$u" >/dev/null 2>&1 && break; sleep 1; done
  done
  RC_URL=http://localhost:8080; DASH_URL=http://localhost:3000/admin; WIDGET_SRC=http://localhost:8081/widget.iife.js
fi

cd "$ROOT/examples/reference-host"
export DASHBOARD_URL="$DASH_URL" WIDGET_URL="$WIDGET_SRC" KEY_ID="$KID" INTEGRATION_SLUG="$SLUG" PRIVATE_KEY_FILE="$WORK/host.private.pem" TENANTS_FILE="$WORK/tenants.json" RASTICHAT_URL="$RC_URL"
node provision.mjs
node server.mjs >"$WORK/host.log" 2>&1 & PIDS+=($!)
for i in $(seq 1 30); do curl -fsS http://localhost:4000/health >/dev/null 2>&1 && break; sleep 1; done

# headless path (no widget): the documented REST + WebSocket protocol only — examples/headless/headless.mjs
(cd "$ROOT/examples/headless" && HEADLESS_ORIGIN="${HEADLESS_ORIGIN:-http://headless.example.test}" node headless.mjs)

cd "$ROOT/e2e"
npx playwright test -c reference-host/playwright.config.ts "$@"
