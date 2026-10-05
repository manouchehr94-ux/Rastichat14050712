#!/usr/bin/env bash
# Read-only preflight for the Django 5.2 ISOLATED staging stack.
# Refuses to run unless isolation is confirmed. Never writes to a database, never runs migrations,
# never touches a deploy target. Run it from the repo root against a staging venv with a synthetic DB.
set -euo pipefail

if [ "${DJANGO52_ISOLATED_STAGING:-}" != "yes-this-is-an-isolated-staging-stack" ]; then
  echo "REFUSING: set DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack to confirm this is an isolated stack with synthetic data." >&2
  exit 2
fi
for var in DB_HOST ALLOWED_HOSTS; do
  value="${!var:-}"
  case ",${value}," in
    *chatchat.rastisi.ir*|*,rastisi.ir,*|*www.rastisi.ir*|*app.rastisi.ir*)
      echo "REFUSING: $var contains a live host ($value)." >&2; exit 2 ;;
  esac
done
if [ "${DB_HOST:-}" = "" ]; then echo "REFUSING: DB_HOST must be set explicitly to the staging database." >&2; exit 2; fi

cd "$(dirname "$0")/../../backend"
PY="${PYTHON:-python}"
fail=0
step() { echo; echo "== $1"; }

step "versions"
"$PY" - <<'PY'
import django, rest_framework, channels, channels_redis
print("django", django.get_version(), "| drf", rest_framework.VERSION, "| channels", channels.__version__, "| channels_redis", channels_redis.__version__)
assert django.VERSION[:2] == (5, 2), "expected Django 5.2"
PY

step "check --deploy (the exact deploy-gate command: --fail-level WARNING --tag security)"
# --tag security is required: without it --fail-level WARNING also trips on drf-spectacular's unrelated schema warnings
# (66 of them on every run), see backend/docker-entrypoint.sh `check-deploy`.
"$PY" manage.py check --deploy --fail-level WARNING --tag security || fail=1

step "migration drift (no DB writes)"
"$PY" manage.py makemigrations --check --dry-run || fail=1

step "showmigrations --plan (read only, pending list)"
"$PY" manage.py showmigrations --plan | grep -c '\[ \]' || true

if command -v pip-audit >/dev/null 2>&1; then
  step "pip-audit"
  pip-audit -r requirements.txt || fail=1
else
  echo "pip-audit not installed: skipped"
fi

step "P1 guardrails"
"$PY" manage.py shell -c "
from django.conf import settings
print('LEGACY_URL_CREDENTIALS_ENABLED =', getattr(settings, 'LEGACY_URL_CREDENTIALS_ENABLED', 'n/a'))
print('WIDGET_REQUIRE_ALLOWED_DOMAINS =', getattr(settings, 'WIDGET_REQUIRE_ALLOWED_DOMAINS', 'n/a'))
" || fail=1

echo
if [ "$fail" -ne 0 ]; then echo "PREFLIGHT FAILED"; exit 1; fi
echo "PREFLIGHT OK"
