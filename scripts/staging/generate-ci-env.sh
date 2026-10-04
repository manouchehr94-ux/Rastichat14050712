#!/bin/bash
# Writes a throwaway .env.staging-shaped file for CI use only — real
# domains/emails are placeholders, DB/Redis passwords and DJANGO_SECRET_KEY
# are freshly random per run (never a fixed string, and long enough to
# clear Django's security.W009 length threshold so
# `check --deploy --fail-level WARNING` is validated against a realistic
# strong secret, not one that happens to dodge the check by accident).
# Never committed, never used outside a CI runner's own throwaway
# filesystem.
#
# Usage: scripts/staging/generate-ci-env.sh <output-path> <media-host-path> <static-host-path> <backup-dir>
set -euo pipefail

OUT="${1:?Usage: generate-ci-env.sh <output-path> <media-host-path> <static-host-path> <backup-dir>}"
MEDIA_HOST_PATH="${2:?Usage: generate-ci-env.sh <output-path> <media-host-path> <static-host-path> <backup-dir>}"
STATIC_HOST_PATH="${3:?Usage: generate-ci-env.sh <output-path> <media-host-path> <static-host-path> <backup-dir>}"
BACKUP_DIR="${4:?Usage: generate-ci-env.sh <output-path> <media-host-path> <static-host-path> <backup-dir>}"

random_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))'; }
DB_PASSWORD="$(random_secret)"

cat > "$OUT" <<EOF
BACKEND_DOMAIN=ci-backend.example.com
OPERATOR_DOMAIN=ci-operator.example.com
PLATFORM_DOMAIN=ci-platform.example.com
CERTBOT_EMAIL=ci@example.com

BACKEND_PORT=8100
OPERATOR_PORT=3100
PLATFORM_PORT=3101
WIDGET_PORT=8180

COMPOSE_PROJECT_NAME=rastichat-ci

ENVIRONMENT=staging
DEBUG=0
DJANGO_SECRET_KEY=$(random_secret)
DJANGO_SETTINGS_MODULE=config.settings

ALLOWED_HOSTS=ci-backend.example.com
CSRF_TRUSTED_ORIGINS=https://ci-backend.example.com
CORS_ALLOWED_ORIGINS=https://ci-operator.example.com,https://ci-platform.example.com

TIME_ZONE=UTC
DJANGO_LOG_LEVEL=INFO

DB_HOST=db
DB_PORT=5432
DB_NAME=rastichat_db
DB_USER=rastichat
DB_PASSWORD=${DB_PASSWORD}
DATABASE_URL=postgres://rastichat:${DB_PASSWORD}@db:5432/rastichat_db
DB_CONN_MAX_AGE=60

REDIS_HOST=redis
REDIS_URL=redis://redis:6379/0

MEDIA_ROOT=/app/media
STATIC_ROOT=/app/staticfiles
MEDIA_HOST_PATH=${MEDIA_HOST_PATH}
STATIC_HOST_PATH=${STATIC_HOST_PATH}
RASTICHAT_UID=10001
RASTICHAT_GID=10001

MEDIA_UPLOAD_MAX_IMAGE_BYTES=8388608
MEDIA_UPLOAD_MAX_VOICE_BYTES=15728640
DATA_UPLOAD_MAX_MEMORY_SIZE=20971520

LOGIN_THROTTLE_RATE=10/min
WIDGET_START_THROTTLE_RATE=20/min
WIDGET_MESSAGE_THROTTLE_RATE=60/min
WIDGET_RATING_THROTTLE_RATE=20/min
KB_FEEDBACK_THROTTLE_RATE=20/min
KB_SEARCH_THROTTLE_RATE=60/min
MACRO_EXECUTION_THROTTLE_RATE=60/min
MEDIA_UPLOAD_THROTTLE_RATE=30/min
WIDGET_WS_MESSAGE_RATE_LIMIT=30
WIDGET_WS_MESSAGE_RATE_WINDOW_SECONDS=60

SECURE_HSTS_SECONDS=3600

NEXT_PUBLIC_API_BASE_URL=https://ci-backend.example.com/api/v1
NEXT_PUBLIC_WS_BASE_URL=wss://ci-backend.example.com/ws

BACKUP_DIR=${BACKUP_DIR}
BACKUP_RETENTION_DAYS=1

MONITORING_TOKEN=$(random_secret)
EOF

echo "Wrote CI env file: $OUT"
