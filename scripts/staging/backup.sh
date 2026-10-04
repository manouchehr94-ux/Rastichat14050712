#!/bin/bash
# Backs up the Postgres database and the media directory. Every output
# filename is timestamped to the second and the script refuses to run if a
# file with that exact name already exists — a backup is never silently
# overwritten. Safe to run from cron/a systemd timer.
#
# Usage: scripts/staging/backup.sh [path-to-env-file] [path-to-compose-file]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-$REPO_ROOT/.env.staging}"
export ENV_FILE
COMPOSE_FILE="${2:-$REPO_ROOT/docker-compose.staging.yml}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

BACKUP_DIR="${BACKUP_DIR:-/opt/rastichat/backups}"
MEDIA_HOST_PATH="${MEDIA_HOST_PATH:-/opt/rastichat/media}"
DB_NAME="${DB_NAME:-rastichat_db}"
DB_USER="${DB_USER:-rastichat}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

DB_DEST="$BACKUP_DIR/rastichat-db-${TIMESTAMP}.sql.gz"
MEDIA_DEST="$BACKUP_DIR/rastichat-media-${TIMESTAMP}.tar.gz"
META_DEST="$BACKUP_DIR/rastichat-meta-${TIMESTAMP}.json"

for f in "$DB_DEST" "$MEDIA_DEST" "$META_DEST"; do
  if [ -e "$f" ]; then
    echo "Refusing to overwrite an existing backup file: $f" >&2
    echo "(This should only happen if backup.sh ran twice within the same second — re-run it.)" >&2
    exit 1
  fi
done

echo "=== Backing up database ($DB_NAME) -> $DB_DEST ==="
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T db \
  pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$DB_DEST"
sha256sum "$DB_DEST" > "${DB_DEST}.sha256"

echo "=== Backing up media ($MEDIA_HOST_PATH) -> $MEDIA_DEST ==="
if [ -d "$MEDIA_HOST_PATH" ]; then
  tar -czf "$MEDIA_DEST" -C "$(dirname "$MEDIA_HOST_PATH")" "$(basename "$MEDIA_HOST_PATH")"
  sha256sum "$MEDIA_DEST" > "${MEDIA_DEST}.sha256"
else
  echo "  (media path $MEDIA_HOST_PATH does not exist yet — skipping, nothing to back up)"
  MEDIA_DEST=""
fi

GIT_SHA="$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null || echo unknown)"
DB_SIZE_BYTES="$(stat -c%s "$DB_DEST" 2>/dev/null || stat -f%z "$DB_DEST")"
if [ -n "$MEDIA_DEST" ]; then
  MEDIA_BACKUP_FILE_JSON="\"$(basename "$MEDIA_DEST")\""
  MEDIA_BACKUP_SHA256_JSON="\"$(awk '{print $1}' "${MEDIA_DEST}.sha256")\""
  MEDIA_SIZE_BYTES="$(stat -c%s "$MEDIA_DEST" 2>/dev/null || stat -f%z "$MEDIA_DEST")"
else
  MEDIA_BACKUP_FILE_JSON="null"
  MEDIA_BACKUP_SHA256_JSON="null"
  MEDIA_SIZE_BYTES="null"
fi
cat > "$META_DEST" <<JSON
{
  "timestamp": "${TIMESTAMP}",
  "database_name": "${DB_NAME}",
  "db_backup_file": "$(basename "$DB_DEST")",
  "db_backup_sha256": "$(awk '{print $1}' "${DB_DEST}.sha256")",
  "db_backup_size_bytes": ${DB_SIZE_BYTES},
  "media_backup_file": ${MEDIA_BACKUP_FILE_JSON},
  "media_backup_sha256": ${MEDIA_BACKUP_SHA256_JSON},
  "media_backup_size_bytes": ${MEDIA_SIZE_BYTES},
  "image_tag": "${IMAGE_TAG:-staging}",
  "git_sha": "${GIT_SHA}"
}
JSON
# Deliberately does NOT copy the .env file itself into the backup — secrets
# (DJANGO_SECRET_KEY, DB_PASSWORD, ...) never belong inside a backup archive
# that might get copied around or retained past its retention window under
# looser handling than the live .env file gets.
echo "=== Wrote metadata -> $META_DEST (no secrets included) ==="

RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
echo "=== Applying retention (${RETENTION_DAYS} days) ==="
find "$BACKUP_DIR" -maxdepth 1 -name 'rastichat-*' -mtime "+${RETENTION_DAYS}" -print -delete

echo
echo "Backup complete:"
echo "  DB:    $DB_DEST"
[ -n "$MEDIA_DEST" ] && echo "  Media: $MEDIA_DEST"
echo "  Meta:  $META_DEST"
