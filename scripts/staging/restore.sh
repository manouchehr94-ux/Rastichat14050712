#!/bin/bash
# Restores a database backup produced by backup.sh. Two modes:
#
#   --target-db=NAME   Restores into a NEW/disposable database of that name
#                       (dropped and recreated fresh first if it already
#                       exists) — the safe default for testing a backup,
#                       never touches the live database.
#
#   (no --target-db)   Restores into the REAL configured database — this
#                       DESTROYS all current data there. Requires --yes.
#
# Usage: scripts/staging/restore.sh <backup-file.sql.gz> [--target-db=NAME] [--yes] [env-file] [compose-file]
set -euo pipefail

BACKUP_FILE="${1:?Usage: restore.sh <backup-file.sql.gz> [--target-db=NAME] [--yes] [env-file] [compose-file]}"
shift

TARGET_DB=""
CONFIRMED=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --target-db=*) TARGET_DB="${arg#--target-db=}" ;;
    --yes) CONFIRMED=1 ;;
    *) POSITIONAL+=("$arg") ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${POSITIONAL[0]:-$REPO_ROOT/.env.staging}"
export ENV_FILE
COMPOSE_FILE="${POSITIONAL[1]:-$REPO_ROOT/docker-compose.staging.yml}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

# Never restore an unverified backup — catches truncated downloads/corrupt
# files before they overwrite anything.
"$REPO_ROOT/scripts/staging/verify-backup.sh" "$BACKUP_FILE"

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
DB_USER="${DB_USER:-rastichat}"
REAL_DB_NAME="${DB_NAME:-rastichat_db}"

if [ -z "$TARGET_DB" ]; then
  if [ "$CONFIRMED" -ne 1 ]; then
    echo "About to restore into the LIVE database '${REAL_DB_NAME}' — this DESTROYS all current" >&2
    echo "data there. Re-run with --yes to confirm, or pass --target-db=some_scratch_name to" >&2
    echo "restore into a disposable database instead (recommended for testing a backup)." >&2
    exit 1
  fi
  TARGET_DB="$REAL_DB_NAME"
  echo "=== Restoring into LIVE database: ${TARGET_DB} ==="
else
  echo "=== Restoring into disposable database: ${TARGET_DB} (live '${REAL_DB_NAME}' is untouched) ==="
fi

run_psql() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db psql -U "$DB_USER" "$@"
}

echo "--- Dropping and recreating ${TARGET_DB} for a clean restore ---"
run_psql -d postgres -c "DROP DATABASE IF EXISTS \"${TARGET_DB}\";"
run_psql -d postgres -c "CREATE DATABASE \"${TARGET_DB}\" OWNER \"${DB_USER}\";"

echo "--- Loading ${BACKUP_FILE} into ${TARGET_DB} ---"
gunzip -c "$BACKUP_FILE" | docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  psql -U "$DB_USER" -d "$TARGET_DB" --set ON_ERROR_STOP=on

echo
echo "Restore complete: ${BACKUP_FILE} -> database '${TARGET_DB}'."
if [ "$TARGET_DB" != "$REAL_DB_NAME" ]; then
  echo "This was a disposable copy — the live database was never touched."
  echo "Run: docker compose -f \"$COMPOSE_FILE\" --env-file \"$ENV_FILE\" exec db psql -U ${DB_USER} -d ${TARGET_DB}"
  echo "to inspect it, then drop it when done:"
  echo "  docker compose -f \"$COMPOSE_FILE\" --env-file \"$ENV_FILE\" exec db psql -U ${DB_USER} -d postgres -c 'DROP DATABASE \"${TARGET_DB}\";'"
fi
