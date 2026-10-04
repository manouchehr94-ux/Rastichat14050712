#!/bin/bash
# Full deploy flow: validate -> fix permissions -> backup (if a stack is
# already running) -> build -> deploy-time security check -> migrate ->
# collectstatic -> deploy -> health check -> smoke test -> done. Stops at
# the first failing step rather than pressing on — see
# docs/runbooks/DEPLOYMENT_ROLLBACK.md for what to do when a step fails.
#
# Usage: scripts/staging/deploy.sh [path-to-env-file] [path-to-compose-file]
set -euo pipefail

TOTAL_STEPS=11
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-$REPO_ROOT/.env.staging}"
export ENV_FILE
COMPOSE_FILE="${2:-$REPO_ROOT/docker-compose.staging.yml}"
COMPOSE="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

echo "=== Step 1/${TOTAL_STEPS}: Validate environment ==="
if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
# `config --quiet` exercises every ${VAR:?...} required-variable guard in
# the compose file (secrets, domains, ...) without starting anything.
$COMPOSE config --quiet
echo "Environment OK."
echo

MEDIA_HOST_PATH="${MEDIA_HOST_PATH:-/opt/rastichat/media}"
STATIC_HOST_PATH="${STATIC_HOST_PATH:-/opt/rastichat/staticfiles}"
BACKUP_DIR="${BACKUP_DIR:-/opt/rastichat/backups}"
RASTICHAT_UID="${RASTICHAT_UID:-10001}"
RASTICHAT_GID="${RASTICHAT_GID:-10001}"
mkdir -p "$MEDIA_HOST_PATH" "$STATIC_HOST_PATH" "$BACKUP_DIR"

GIT_SHA="$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null || echo unknown)"
echo "Deploying commit: ${GIT_SHA}"
echo

echo "=== Step 2/${TOTAL_STEPS}: Fix media/static directory permissions ==="
"$REPO_ROOT/scripts/staging/fix-permissions.sh" "$MEDIA_HOST_PATH" "$STATIC_HOST_PATH" "$RASTICHAT_UID" "$RASTICHAT_GID"
echo

echo "=== Step 3/${TOTAL_STEPS}: Backup (skipped if this is the first-ever deploy) ==="
if $COMPOSE ps db --status running 2>/dev/null | grep -q db; then
  "$REPO_ROOT/scripts/staging/backup.sh" "$ENV_FILE" "$COMPOSE_FILE"
else
  echo "No running 'db' service found — nothing to back up yet (first deploy)."
fi
echo

echo "=== Step 4/${TOTAL_STEPS}: Build images (GIT_SHA=${GIT_SHA}) ==="
$COMPOSE build \
  --build-arg GIT_SHA="$GIT_SHA" \
  --build-arg RASTICHAT_UID="$RASTICHAT_UID" \
  --build-arg RASTICHAT_GID="$RASTICHAT_GID" \
  --build-arg NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:?NEXT_PUBLIC_API_BASE_URL must be set}" \
  --build-arg NEXT_PUBLIC_WS_BASE_URL="${NEXT_PUBLIC_WS_BASE_URL:?NEXT_PUBLIC_WS_BASE_URL must be set}"
# Also tag each image with the immutable git SHA (in addition to the
# moving :${IMAGE_TAG:-staging} tag docker-compose.staging.yml itself
# uses) — this is what scripts/staging/rollback.sh retags back onto the
# moving tag to roll back to a previous build WITHOUT needing to rebuild
# or check out old source.
for image in rastichat-backend rastichat-operator-dashboard rastichat-platform-dashboard rastichat-widget; do
  docker tag "${image}:${IMAGE_TAG:-staging}" "${image}:${GIT_SHA}"
done
echo

echo "=== Step 5/${TOTAL_STEPS}: Wait for db/redis ==="
$COMPOSE up -d db redis
echo "Waiting for db/redis to report healthy..."
for i in $(seq 1 30); do
  db_health="$($COMPOSE ps db --format '{{.Health}}' 2>/dev/null || echo '')"
  redis_health="$($COMPOSE ps redis --format '{{.Health}}' 2>/dev/null || echo '')"
  if [ "$db_health" = "healthy" ] && [ "$redis_health" = "healthy" ]; then
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "db/redis did not become healthy within 60s — aborting before touching migrations." >&2
    exit 1
  fi
done
echo

echo "=== Step 6/${TOTAL_STEPS}: Deploy-time Django security check ==="
# Runs the JUST-BUILT image's own settings.py against the real staging/
# production env file, BEFORE any migration or replica replacement — a
# regression here (weak SECRET_KEY, wildcard hosts, missing CSRF/CORS
# origins, DEBUG=1) aborts the deploy while the previous, still-good
# deployment keeps serving traffic untouched. `--fail-level WARNING` is
# required: `check --deploy` alone reports a weak SECRET_KEY as
# security.W009, which is WARNING-level and would otherwise exit 0.
$COMPOSE run --rm backend check-deploy
echo

echo "=== Step 7/${TOTAL_STEPS}: Run migrations (one-off, before any replica starts on the new image) ==="
$COMPOSE run --rm backend migrate
echo

echo "=== Step 8/${TOTAL_STEPS}: Collect static files ==="
# One-off, same pattern as migrate — never run from every `web` replica's
# own startup, so N replicas never race each other collecting into the
# same STATIC_HOST_PATH. Must run after Step 2 so the backend user can
# actually write into the (now correctly owned) bind-mounted STATIC_ROOT.
$COMPOSE run --rm backend collectstatic
echo

echo "=== Step 9/${TOTAL_STEPS}: Deploy services ==="
$COMPOSE up -d
echo

echo "=== Step 10/${TOTAL_STEPS}: Health check ==="
BACKEND_PORT="${BACKEND_PORT:-8100}"
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/v1/health/ready/" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
if [ "$healthy" -ne 1 ]; then
  echo "Backend did not become ready within 60s after deploy." >&2
  echo "Run scripts/staging/status.sh to inspect, and scripts/staging/rollback.sh if needed." >&2
  exit 1
fi
echo "Backend is ready."
echo

echo "=== Step 11/${TOTAL_STEPS}: Smoke test ==="
OPERATOR_PORT="${OPERATOR_PORT:-3100}"
PLATFORM_PORT="${PLATFORM_PORT:-3101}"
WIDGET_PORT="${WIDGET_PORT:-8180}"
smoke_failed=0
for check in \
  "backend:http://127.0.0.1:${BACKEND_PORT}/api/v1/health/ready/" \
  "operator-dashboard:http://127.0.0.1:${OPERATOR_PORT}/" \
  "platform-dashboard:http://127.0.0.1:${PLATFORM_PORT}/" \
  "widget:http://127.0.0.1:${WIDGET_PORT}/widget.js"
do
  name="${check%%:*}"; url="${check#*:}"
  code="$(curl -fsS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo ERR)"
  if [ "$code" = "ERR" ] || [ "${code:0:1}" = "5" ]; then
    echo "  ✗ ${name} (${url}) -> ${code}"
    smoke_failed=1
  else
    echo "  ✓ ${name} (${url}) -> ${code}"
  fi
done
if [ "$smoke_failed" -ne 0 ]; then
  echo "Smoke test FAILED — see docs/runbooks/DEPLOYMENT_ROLLBACK.md." >&2
  exit 1
fi
echo

echo "############################################################"
echo "# Deployment successful: ${GIT_SHA}"
echo "# $(date -Iseconds)"
echo "############################################################"
