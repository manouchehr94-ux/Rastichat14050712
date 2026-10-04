#!/bin/bash
# Current deployment status: container health, image tags/git SHA in use,
# health/readiness/monitoring endpoint results, latest backup. Read-only.
#
# Usage: scripts/staging/status.sh [path-to-env-file] [path-to-compose-file]
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-$REPO_ROOT/.env.staging}"
export ENV_FILE
COMPOSE_FILE="${2:-$REPO_ROOT/docker-compose.staging.yml}"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

echo "=== Containers ==="
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps 2>&1 || echo "(compose ps failed — is the stack up?)"
echo

echo "=== Backend image ==="
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}} (built {{.Created}})' \
  "rastichat-backend:${IMAGE_TAG:-staging}" 2>/dev/null \
  || echo "(no git-sha label found — see docker/*.Dockerfile LABEL / scripts/staging/deploy.sh build step)"
echo

BACKEND_PORT="${BACKEND_PORT:-8100}"
STATUS_BODY_FILE="$(mktemp)"
echo "=== Health endpoints (via 127.0.0.1:${BACKEND_PORT}, bypassing Nginx) ==="
for path in health/live health/ready health/monitoring; do
  # health/monitoring requires the same shared-secret header a real
  # monitoring tool would send (see common.views.MonitoringView /
  # MONITORING_TOKEN) — this operator-run script has the env file's value
  # available, an anonymous caller does not.
  code="$(curl -fsS -o "$STATUS_BODY_FILE" -w '%{http_code}' \
    ${MONITORING_TOKEN:+-H "X-Monitoring-Token: ${MONITORING_TOKEN}"} \
    "http://127.0.0.1:${BACKEND_PORT}/api/v1/${path}/" 2>/dev/null || echo "ERR")"
  echo "--- /api/v1/${path}/ -> ${code} ---"
  cat "$STATUS_BODY_FILE" 2>/dev/null | (python3 -m json.tool 2>/dev/null || cat)
  echo
done
rm -f "$STATUS_BODY_FILE"

BACKUP_DIR="${BACKUP_DIR:-/opt/rastichat/backups}"
echo "=== Latest backup ==="
if [ -d "$BACKUP_DIR" ]; then
  latest="$(find "$BACKUP_DIR" -maxdepth 1 -name 'rastichat-db-*.sql.gz' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
  if [ -n "$latest" ]; then
    echo "  $latest ($(date -d "@$(stat -c %Y "$latest")" -Iseconds 2>/dev/null || stat -f %Sm "$latest"))"
  else
    echo "  No backups found in $BACKUP_DIR yet."
  fi
else
  echo "  Backup directory $BACKUP_DIR does not exist yet."
fi
