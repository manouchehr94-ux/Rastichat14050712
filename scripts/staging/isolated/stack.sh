#!/usr/bin/env bash
# Isolated Django 5.2 staging stack on a DISPOSABLE Linux host (a throw-away VM/sandbox, never the live VPS).
# Process-based (no Docker): own PostgreSQL cluster, own Redis, N Daphne workers, an L4 balancer, a widget static
# server, two Next.js dashboards and the REAL deploy/nginx templates (rendered by scripts/nginx/install-sites.sh)
# with TLS from a throw-away CA. All data synthetic. Refuses to run unless isolation is confirmed.
#
#   STG_DIR=/path SRC=/path/to/repo-checkout ./stack.sh <command>
#   commands: init-env | datastores | backend | frontends | nginx | start | stop | status | seed | all
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${STG_DIR:?STG_DIR (working directory of the staging stack, outside the repo) is required}"
: "${SRC:?SRC (checkout of the version under test) is required}"
if [ "${DJANGO52_ISOLATED_STAGING:-}" != "yes-this-is-an-isolated-staging-stack" ]; then
  echo "REFUSING: set DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack (disposable host, synthetic data only)." >&2; exit 2
fi
if [ -e /opt/rastisi-next ] || [ -e /opt/rastichat ]; then
  echo "REFUSING: this host has /opt/rastisi-next or /opt/rastichat — it looks like the live server." >&2; exit 2
fi

ENV_FILE="$STG_DIR/env.staging"
PY="$STG_DIR/venv/bin/python"
PGPORT="${PGPORT:-55432}"; REDISPORT="${REDISPORT:-56379}"
WORKERS="${WORKERS:-2}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PG_DIR="${PG_DIR:-/var/tmp/rastichat_stg_pg}"   # must be reachable by the postgres OS user
BACKEND_DOMAIN="${BACKEND_DOMAIN:-chat-stg.example.test}"
OPERATOR_DOMAIN="${OPERATOR_DOMAIN:-operator-stg.example.test}"
PLATFORM_DOMAIN="${PLATFORM_DOMAIN:-platform-stg.example.test}"
WWW_DIR="${WWW_DIR:-/var/tmp/rastichat_stg_www}"   # served by nginx (worker user must be able to traverse it)
mkdir -p "$STG_DIR"/{logs,run,certs,backups} "$WWW_DIR"/{media,static,widget}; chmod 755 "$WWW_DIR" "$WWW_DIR"/*

load_env() { set -a; . "$ENV_FILE"; set +a; }
rand() { python3 -c "import secrets;print(secrets.token_urlsafe($1))"; }

init_env() {
  [ -f "$ENV_FILE" ] && { echo "env exists: $ENV_FILE (kept)"; return; }
  ( umask 077; cat > "$ENV_FILE" <<ENV
# generated for THIS staging stack only; every secret is fresh — nothing here is a production value
BACKEND_DOMAIN=$BACKEND_DOMAIN
OPERATOR_DOMAIN=$OPERATOR_DOMAIN
PLATFORM_DOMAIN=$PLATFORM_DOMAIN
BACKEND_PORT=8100
OPERATOR_PORT=3100
PLATFORM_PORT=3101
WIDGET_PORT=8180
ENVIRONMENT=staging
DEBUG=0
DJANGO_SETTINGS_MODULE=config.settings
DJANGO_SECRET_KEY=$(rand 60)
ALLOWED_HOSTS=$BACKEND_DOMAIN,$OPERATOR_DOMAIN,$PLATFORM_DOMAIN
CSRF_TRUSTED_ORIGINS=https://$OPERATOR_DOMAIN,https://$PLATFORM_DOMAIN
CORS_ALLOWED_ORIGINS=https://$OPERATOR_DOMAIN,https://$PLATFORM_DOMAIN,https://$BACKEND_DOMAIN
TIME_ZONE=UTC
DJANGO_LOG_LEVEL=INFO
DB_PASSWORD_PLAIN=$(rand 24)
REDIS_PASSWORD_PLAIN=$(rand 24)
MONITORING_TOKEN=$(rand 32)
MEDIA_ROOT=$WWW_DIR/media
STATIC_ROOT=$WWW_DIR/static
MEDIA_HOST_PATH=$WWW_DIR/media
STATIC_HOST_PATH=$WWW_DIR/static
CHAT_ATTACHMENTS_PUBLIC=${CHAT_ATTACHMENTS_PUBLIC:-0}
NEXT_PUBLIC_API_BASE_URL=https://$BACKEND_DOMAIN/api/v1
NEXT_PUBLIC_WS_BASE_URL=wss://$BACKEND_DOMAIN/ws
WS_REVALIDATE_SECONDS=5
# the matrix drives everything from one IP: raise the per-IP DRF throttles (nginx limits stay at their real values)
LOGIN_THROTTLE_RATE=600/min
WIDGET_START_THROTTLE_RATE=600/min
WIDGET_INIT_THROTTLE_RATE=600/min
WIDGET_MESSAGE_THROTTLE_RATE=600/min
WIDGET_SESSION_THROTTLE_RATE=600/min
WS_TICKET_THROTTLE_RATE=1200/min
MEDIA_UPLOAD_THROTTLE_RATE=600/min
SUPPORT_WRITE_THROTTLE_RATE=600/min
ENV
  )
  # DATABASE_URL / REDIS_URL derive from the generated secrets
  . "$ENV_FILE"
  { echo "DATABASE_URL=postgres://rastichat_stg:${DB_PASSWORD_PLAIN}@127.0.0.1:$PGPORT/rastichat_stg"
    echo "DB_HOST=127.0.0.1"; echo "DB_USER=rastichat_stg"; echo "DB_PASSWORD=${DB_PASSWORD_PLAIN}"; echo "DB_NAME=rastichat_stg"; echo "DB_PORT=$PGPORT"
    echo "REDIS_URL=redis://:${REDIS_PASSWORD_PLAIN}@127.0.0.1:$REDISPORT/0"; } >> "$ENV_FILE"
  echo "wrote $ENV_FILE (chmod 600)"
}

datastores() {
  load_env
  if [ ! -d "$PG_DIR/base" ]; then
    mkdir -p "$PG_DIR"; chown postgres:postgres "$PG_DIR"; chmod 700 "$PG_DIR"
    pwf=$(mktemp /var/tmp/stgpw.XXXXXX); echo "$DB_PASSWORD_PLAIN" > "$pwf"; chown postgres "$pwf"; chmod 600 "$pwf"
    su postgres -c "$PGBIN/initdb -D $PG_DIR -U rastichat_stg --pwfile=$pwf -A scram-sha-256 >/dev/null"
    rm -f "$pwf"
  fi
  mkdir -p /var/tmp/rastichat_stg_sock; chown postgres /var/tmp/rastichat_stg_sock
  su postgres -c "$PGBIN/pg_ctl -D $PG_DIR -o '-p $PGPORT -c listen_addresses=127.0.0.1 -k /var/tmp/rastichat_stg_sock' -l /var/tmp/rastichat_stg_postgres.log -w start" >/dev/null || true
  PGPASSWORD="$DB_PASSWORD_PLAIN" psql -h 127.0.0.1 -p $PGPORT -U rastichat_stg -d postgres -tc "select 1 from pg_database where datname='rastichat_stg'" | grep -q 1 || \
    PGPASSWORD="$DB_PASSWORD_PLAIN" createdb -h 127.0.0.1 -p $PGPORT -U rastichat_stg rastichat_stg
  mkdir -p "$STG_DIR/redis"
  redis-cli -p $REDISPORT -a "$REDIS_PASSWORD_PLAIN" ping >/dev/null 2>&1 || \
    redis-server --port $REDISPORT --bind 127.0.0.1 --requirepass "$REDIS_PASSWORD_PLAIN" --appendonly yes --dir "$STG_DIR/redis" --daemonize yes --logfile "$STG_DIR/logs/redis.log" >/dev/null
  echo "postgres :$PGPORT and redis :$REDISPORT up"
}

backend() {
  [ -x "$PY" ] || python3 -m venv "$STG_DIR/venv"
  "$STG_DIR/venv/bin/pip" install -q -r "$SRC/backend/requirements-dev.txt"
  load_env; cd "$SRC/backend"
  "$PY" manage.py check --deploy --fail-level WARNING --tag security
  "$PY" manage.py collectstatic --noinput >/dev/null
  echo "backend deps + static ready"
}

frontends() {
  load_env
  ( cd "$SRC/packages/widget" && npm ci --silent && npm run build --silent )
  mkdir -p "$WWW_DIR/widget/widget/stg"
  cp "$SRC/packages/widget/dist/widget.iife.js" "$WWW_DIR/widget/widget.js"
  cp "$SRC/packages/widget/dist/widget.iife.js" "$WWW_DIR/widget/widget/stg/widget.js"
  for app in operator platform; do
    ( cd "$SRC/apps/$app-dashboard" && npm ci --silent && NODE_ENV=production npm run build --silent )
    rm -rf "$STG_DIR/dash-$app"; mkdir -p "$STG_DIR/dash-$app"
    cp -r "$SRC/apps/$app-dashboard/.next/standalone/." "$STG_DIR/dash-$app/"
    mkdir -p "$STG_DIR/dash-$app/.next"; cp -r "$SRC/apps/$app-dashboard/.next/static" "$STG_DIR/dash-$app/.next/static"
    [ -d "$SRC/apps/$app-dashboard/public" ] && cp -r "$SRC/apps/$app-dashboard/public" "$STG_DIR/dash-$app/public"
  done
  echo "widget + dashboards built"
}

certs() {
  local d="$STG_DIR/certs"
  [ -f "$d/ca.pem" ] || { openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=rastichat-staging-throwaway-CA" -keyout "$d/ca.key" -out "$d/ca.pem" 2>/dev/null; }
  for dom in "$BACKEND_DOMAIN" "$OPERATOR_DOMAIN" "$PLATFORM_DOMAIN"; do
    live="/etc/letsencrypt/live/$dom"; mkdir -p "$live"
    [ -f "$live/fullchain.pem" ] && continue
    openssl req -newkey rsa:2048 -nodes -subj "/CN=$dom" -keyout "$live/privkey.pem" -out "$d/$dom.csr" 2>/dev/null
    printf "subjectAltName=DNS:%s\n" "$dom" > "$d/$dom.ext"
    openssl x509 -req -in "$d/$dom.csr" -CA "$d/ca.pem" -CAkey "$d/ca.key" -CAcreateserial -days 30 -extfile "$d/$dom.ext" -out "$live/fullchain.pem" 2>/dev/null
    cat "$d/ca.pem" >> "$live/fullchain.pem"
  done
  for dom in "$BACKEND_DOMAIN" "$OPERATOR_DOMAIN" "$PLATFORM_DOMAIN"; do
    grep -q " $dom" /etc/hosts || echo "127.0.0.1 $dom" >> /etc/hosts
  done
}

nginx_setup() {
  load_env; certs
  # the REAL deploy path: scripts/nginx/install-sites.sh renders deploy/nginx/*.template with the staging env
  cat > "$STG_DIR/nginx-sites.env" <<NE
BACKEND_DOMAIN=$BACKEND_DOMAIN
OPERATOR_DOMAIN=$OPERATOR_DOMAIN
PLATFORM_DOMAIN=$PLATFORM_DOMAIN
BACKEND_PORT=8100
OPERATOR_PORT=3100
PLATFORM_PORT=3101
WIDGET_PORT=8180
MEDIA_HOST_PATH=$WWW_DIR/media
STATIC_HOST_PATH=$WWW_DIR/static
NE
  rm -f /etc/nginx/sites-enabled/default
  ( cd "$SRC" && bash scripts/nginx/install-sites.sh "$STG_DIR/nginx-sites.env" ) || true
  # sandbox deviation only: this host has no IPv6, so drop the `listen [::]` lines from the RENDERED copies (the repo templates are untouched)
  if ! ip -6 addr show 2>/dev/null | grep -q inet6; then sed -i '/listen \[::\]/d' /etc/nginx/sites-available/rastichat-*.conf; fi
  nginx -t
  # aux nginx: widget static 8180 (L4 balancer 8100 -> daphne workers is tcp-balancer.mjs, see start()) (the roles of docker/nginx + widget container)
  cat > "$STG_DIR/aux-nginx.conf" <<AUX
pid $STG_DIR/run/aux-nginx.pid; error_log $STG_DIR/logs/aux-nginx-error.log; events {}
http {
  include /etc/nginx/mime.types; access_log off; map \$http_upgrade \$cu { default upgrade; '' close; }
  client_body_temp_path $STG_DIR/run/b; proxy_temp_path $STG_DIR/run/p; fastcgi_temp_path $STG_DIR/run/f; uwsgi_temp_path $STG_DIR/run/u; scgi_temp_path $STG_DIR/run/s;
  server { listen 127.0.0.1:8180; root $WWW_DIR/widget;
    location = /widget.js { add_header Cache-Control "no-cache, must-revalidate"; }
    location ~ ^/widget/[^/]+/widget\.js\$ { add_header Cache-Control "public, max-age=31536000, immutable"; } }
}
AUX
  nginx -t -c "$STG_DIR/aux-nginx.conf" -p "$STG_DIR/run/" 2>&1 | tail -1
}

# Two REAL storefront origins served over TLS by the same nginx (an allowed and a forbidden embedding site), so the
# browser tests exercise true cross-origin behaviour instead of faked routes.
embed_setup() {
  load_env; mkdir -p "$WWW_DIR/embed"; chmod 755 "$WWW_DIR/embed"
  local d="$STG_DIR/certs"
  for dom in embed-allowed.example.test embed-forbidden.example.test; do
    live="/etc/letsencrypt/live/$dom"; mkdir -p "$live"
    if [ ! -f "$live/fullchain.pem" ]; then
      openssl req -newkey rsa:2048 -nodes -subj "/CN=$dom" -keyout "$live/privkey.pem" -out "$d/$dom.csr" 2>/dev/null
      printf "subjectAltName=DNS:%s\n" "$dom" > "$d/$dom.ext"
      openssl x509 -req -in "$d/$dom.csr" -CA "$d/ca.pem" -CAkey "$d/ca.key" -CAcreateserial -days 30 -extfile "$d/$dom.ext" -out "$live/fullchain.pem" 2>/dev/null
    fi
    grep -q " $dom" /etc/hosts || echo "127.0.0.1 $dom" >> /etc/hosts
    cat > "/etc/nginx/sites-available/rastichat-stg-$dom.conf" <<SITE
server { listen 443 ssl; server_name $dom;
  ssl_certificate /etc/letsencrypt/live/$dom/fullchain.pem; ssl_certificate_key /etc/letsencrypt/live/$dom/privkey.pem;
  root $WWW_DIR/embed; default_type text/html;
  location = /embed { try_files /embed.html =404; } }
SITE
    ln -sf "/etc/nginx/sites-available/rastichat-stg-$dom.conf" "/etc/nginx/sites-enabled/rastichat-stg-$dom.conf"
  done
  local key; key=$(python3 -c "import json;print(json.load(open('$STG_DIR/seed-extra.json'))['A']['project_key'])")
  cat > "$WWW_DIR/embed/embed.html" <<HTML
<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>storefront</title></head>
<body><h1>فروشگاه آزمایشی</h1><script src="https://$BACKEND_DOMAIN/widget.js"></script>
<script>window.addEventListener('load', function () { window.RastiChat.init({ projectKey: '$key', apiBase: 'https://$BACKEND_DOMAIN/api/v1', wsBase: 'wss://$BACKEND_DOMAIN/ws' }); });</script></body></html>
HTML
  chmod 644 "$WWW_DIR/embed/embed.html"
  nginx -t && nginx -s reload
}

start() {
  load_env; cd "$SRC/backend"
  pgrep -f "nginx: master process nginx -c $STG_DIR/aux-nginx.conf" >/dev/null || nginx -c "$STG_DIR/aux-nginx.conf" -p "$STG_DIR/run/"
  # L4 (not L7) balancer: the front nginx must see Daphne's raw response headers, X-Accel-Redirect included
  if ! { [ -f "$STG_DIR/run/balancer.pid" ] && kill -0 "$(cat "$STG_DIR/run/balancer.pid")" 2>/dev/null; }; then
    local bports=""; for i in $(seq 1 "$WORKERS"); do bports="$bports $((8100+i))"; done
    setsid nohup node "$(dirname "$0")/tcp-balancer.mjs" 8100 $bports >> "$STG_DIR/logs/balancer.log" 2>&1 &
    echo $! > "$STG_DIR/run/balancer.pid"
  fi
  for i in $(seq 1 "$WORKERS"); do
    port=$((8100+i)); pidf="$STG_DIR/run/daphne$i.pid"
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then continue; fi
    setsid nohup "$PY" -m daphne -b 127.0.0.1 -p $port --application-close-timeout 10 config.asgi:application >> "$STG_DIR/logs/daphne$i.log" 2>&1 &
    echo $! > "$pidf"
  done
  for app in operator platform; do
    port=3100; [ "$app" = platform ] && port=3101; pidf="$STG_DIR/run/dash-$app.pid"
    if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then continue; fi
    ( cd "$STG_DIR/dash-$app" && PORT=$port HOSTNAME=127.0.0.1 NODE_ENV=production setsid nohup node server.js >> "$STG_DIR/logs/dash-$app.log" 2>&1 & echo $! > "$pidf" )
  done
  nginx -t >/dev/null 2>&1 && { pgrep -x nginx >/dev/null && nginx -s reload || nginx; }
  sleep 3; status
}

kill_port() { local pid; pid=$(ss -ltnp "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2); [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; }
stop_dashboards() { for f in "$STG_DIR"/run/dash-*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done; kill_port 3100; kill_port 3101; sleep 1; }

stop() {
  stop_dashboards
  stop_workers
  for f in "$STG_DIR"/run/daphne*.pid "$STG_DIR"/run/dash-*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done
  [ -f "$STG_DIR/run/balancer.pid" ] && kill "$(cat "$STG_DIR/run/balancer.pid")" 2>/dev/null || true; rm -f "$STG_DIR/run/balancer.pid"
  [ -f "$STG_DIR/run/aux-nginx.pid" ] && kill "$(cat "$STG_DIR/run/aux-nginx.pid")" 2>/dev/null || true
  pgrep -x nginx >/dev/null && nginx -s quit || true
}

# SIGTERM, then SIGKILL after 10 s (what docker/systemd do): a Daphne that still has WebSocket clients never exits on SIGTERM alone,
# and the orphan keeps its database connections — repeated restarts exhausted PostgreSQL's max_connections during this very run.
stop_workers() {
  local pids=() f p alive
  for f in "$STG_DIR"/run/daphne*.pid; do [ -f "$f" ] && pids+=("$(cat "$f")"); rm -f "$f"; done
  [ ${#pids[@]} -gt 0 ] || return 0
  kill "${pids[@]}" 2>/dev/null
  for _ in $(seq 1 10); do
    alive=0; for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null && alive=1; done
    [ $alive = 0 ] && return 0; sleep 1
  done
  kill -9 "${pids[@]}" 2>/dev/null; sleep 1; return 0
}

# kill + start the ASGI workers only (a "backend restart": every open WebSocket is dropped by the server)
restart_backend() {
  load_env
  stop_workers
  start >/dev/null
}

# server-side state change for the revocation tests: stack.sh admin <action> [arg]  (see admin_action.py)
admin() {
  load_env; cd "$SRC/backend"
  ADMIN_ACTION="$1" ADMIN_ARG="${2:-}" "$PY" manage.py shell < "$HERE/admin_action.py" 2>&1 | tail -1
}

# environment file for the Playwright suite / soak / rollback scripts, derived from the seed (so a re-seed refreshes it)
test_env() {
  load_env
  local out="${TEST_ENV_OUT:-$STG_DIR/test-env.sh}"
  python3 - "$out" <<PY
import json, os, sys
d = json.load(open("$STG_DIR/seed-extra.json")); u = d["users"]["agent-a1"]
stack = "env STG_DIR=$STG_DIR SRC=$SRC DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack bash $HERE/stack.sh"
hosts = "$BACKEND_DOMAIN $OPERATOR_DOMAIN $PLATFORM_DOMAIN embed-allowed.example.test embed-forbidden.example.test".split()
lines = [
 "export DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack",
 "export SMOKE_BACKEND_URL=https://$BACKEND_DOMAIN SMOKE_WS_URL=wss://$BACKEND_DOMAIN",
 "export SMOKE_OPERATOR_URL=https://$OPERATOR_DOMAIN/admin SMOKE_PLATFORM_URL=https://$PLATFORM_DOMAIN/platform",
 "export SMOKE_WIDGET_URL=https://$BACKEND_DOMAIN/widget.js SMOKE_PROJECT_KEY=" + d["A"]["project_key"],
 "export SMOKE_OPERATOR_EMAIL=" + u["email"] + " SMOKE_OPERATOR_PASSWORD='" + u["password"] + "'",
 "export DJANGO52_ALLOWED_EMBED_ORIGIN=https://embed-allowed.example.test DJANGO52_FORBIDDEN_EMBED_ORIGIN=https://embed-forbidden.example.test",
 "export DJANGO52_EXPECT_LEGACY_URL_OFF=\${DJANGO52_EXPECT_LEGACY_URL_OFF:-1} DJANGO52_IGNORE_HTTPS_ERRORS=1 DJANGO52_REAL_EMBED_SITES=1",
 "export NO_PROXY=" + ",".join(hosts + ["127.0.0.1", "localhost"]) + " no_proxy=" + ",".join(hosts + ["127.0.0.1", "localhost"]),
 "export NODE_EXTRA_CA_CERTS=$STG_DIR/certs/ca.pem",
 "export PW_CHROMIUM_PATH=\${PW_CHROMIUM_PATH:-/opt/pw-browsers/chromium-1194/chrome-linux/chrome}",
 "export DJANGO52_HOST_RULES=\"" + ", ".join(f"MAP {h} 127.0.0.1" for h in hosts) + "\"",
 "export DJANGO52_RESTART_BACKEND_CMD=\"" + stack + " restart-backend\"",
 "export DJANGO52_SEED_JSON=$STG_DIR/seed-extra.json DJANGO52_ADMIN_CMD=\"" + stack + " admin\"",
 "export DJANGO52_WORKER_URLS=http://127.0.0.1:8101,http://127.0.0.1:8102",
 "export DJANGO52_CHAT_MEDIA_CLOSED=\${DJANGO52_CHAT_MEDIA_CLOSED:-0}",
 "export STG_DIR=$STG_DIR REDISPORT=$REDISPORT PGPORT=$PGPORT REDIS_PASSWORD_PLAIN=$REDIS_PASSWORD_PLAIN DB_PASSWORD_PLAIN=$DB_PASSWORD_PLAIN",
]
open(sys.argv[1], "w").write("\n".join(lines) + "\n"); os.chmod(sys.argv[1], 0o600)
PY
  echo "wrote $out"
}

status() {
  load_env
  for u in "https://$BACKEND_DOMAIN/api/v1/health/ready/" "https://$OPERATOR_DOMAIN/admin/login" "https://$PLATFORM_DOMAIN/platform/login" "https://$BACKEND_DOMAIN/widget.js"; do
    printf '%-70s %s\n' "$u" "$(curl -s --noproxy '*' --cacert "$STG_DIR/certs/ca.pem" -o /dev/null -w '%{http_code}' "$u")"
  done
}

seed() {
  load_env; cd "$SRC/backend"
  "$PY" manage.py seed_staging_data --yes --output "$STG_DIR/seed-credentials.txt" >/dev/null
  chmod 600 "$STG_DIR/seed-credentials.txt"
  STG_DIR="$STG_DIR" "$PY" manage.py shell < "$HERE/seed_extra.py"
}

case "${1:-}" in
  init-env) init_env;; datastores) datastores;; backend) backend;; frontends) frontends;;
  nginx) nginx_setup;; embed) embed_setup;; start) start;; admin) shift; admin "$@";; test-env) test_env;; restart-backend) restart_backend;; restart-dashboards) load_env; stop_dashboards; start >/dev/null;; stop) stop;; status) status;; seed) seed;;
  *) echo "usage: $0 init-env|datastores|backend|frontends|nginx|start|stop|status|seed|all"; exit 1;;
esac
