#!/bin/bash
# Rolls back to a previously deployed commit's images (tagged by
# deploy.sh at build time — see its Step 3) and, optionally, restores the
# database from a specific backup. Never runs `docker compose down -v`
# (that destroys the named volumes — Postgres/Redis/media/static data —
# which is never what a rollback should do).
#
# Code/image rollback:
#   scripts/staging/rollback.sh <git-sha> [env-file] [compose-file]
#     Retags rastichat-{backend,operator-dashboard,platform-dashboard,widget}:<git-sha>
#     onto the moving :${IMAGE_TAG:-staging} tag and restarts. Requires
#     that <git-sha> was actually built by a previous deploy.sh run on
#     this host (images are local, not fetched from anywhere) — `docker
#     images` lists what's available.
#
# Database rollback (separate, explicit, DESTRUCTIVE — never automatic):
#   scripts/staging/rollback.sh <git-sha> --restore-db=<path-to-backup.sql.gz> --yes
#
# Migration rollback limitation: Django migrations are not automatically
# reversed by this script. Rolling back code past a migration that isn't
# purely additive (renamed/dropped a column the old code doesn't expect)
# requires either a compatible `manage.py migrate <app> <prior_migration>`
# run by hand first, or restoring the database backup taken before that
# migration ran (see --restore-db above) — there is no way to make this
# automatic in general, since not every migration has a safe, lossless
# reverse.
set -euo pipefail

TARGET_SHA="${1:?Usage: rollback.sh <git-sha> [--restore-db=path] [--yes] [env-file] [compose-file]}"
shift

RESTORE_DB=""
CONFIRMED=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --restore-db=*) RESTORE_DB="${arg#--restore-db=}" ;;
    --yes) CONFIRMED=1 ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${POSITIONAL[0]:-$REPO_ROOT/.env.staging}"
export ENV_FILE
COMPOSE_FILE="${POSITIONAL[1]:-$REPO_ROOT/docker-compose.staging.yml}"
COMPOSE="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

echo "=== Retagging images to commit ${TARGET_SHA} ==="
for image in rastichat-backend rastichat-operator-dashboard rastichat-platform-dashboard rastichat-widget; do
  if ! docker image inspect "${image}:${TARGET_SHA}" >/dev/null 2>&1; then
    echo "No local image ${image}:${TARGET_SHA} — was this commit ever deployed with deploy.sh on this host?" >&2
    exit 1
  fi
  docker tag "${image}:${TARGET_SHA}" "${image}:${IMAGE_TAG:-staging}"
done

if [ -n "$RESTORE_DB" ]; then
  echo "=== Database rollback requested: ${RESTORE_DB} ==="
  if [ "$CONFIRMED" -ne 1 ]; then
    echo "--restore-db was given without --yes — this DESTROYS all data written to the live" >&2
    echo "database since that backup was taken. Re-run with --yes to confirm." >&2
    exit 1
  fi
  "$REPO_ROOT/scripts/staging/restore.sh" "$RESTORE_DB" --yes "$ENV_FILE" "$COMPOSE_FILE"
fi

echo "=== Restarting services on the rolled-back images (no down -v — volumes are untouched) ==="
$COMPOSE up -d

echo
echo "=== Health check ==="
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
  echo "Backend did not become ready within 60s after rollback — investigate immediately." >&2
  exit 1
fi

echo "Rollback to ${TARGET_SHA} complete and healthy."
