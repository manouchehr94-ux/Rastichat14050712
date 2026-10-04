#!/bin/bash
# Read-only report of the VPS's current state, meant to be run BEFORE the
# very first `deploy.sh` on a host that may already run another site.
# Changes nothing — every command below is inspection only.
#
# Usage: scripts/staging/preflight.sh [path-to-env-file]
set -uo pipefail  # deliberately not -e: a missing tool (e.g. no docker yet
                   # on a truly fresh host) should be reported, not abort the report

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-$REPO_ROOT/.env.staging}"

echo "############################################################"
echo "# RastiChat staging preflight report — $(date -Iseconds)"
echo "# Read-only. Nothing on this host is changed by running this."
echo "############################################################"
echo

echo "=== OS ==="
cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)=' || echo "(could not read /etc/os-release)"
echo

echo "=== Disk ==="
df -h / 2>/dev/null || echo "(df unavailable)"
echo

echo "=== Memory ==="
free -h 2>/dev/null || echo "(free unavailable)"
echo

echo "=== Existing Docker containers (ALL, not just RastiChat's) ==="
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>&1 || echo "(docker ps failed — is the daemon running / do you have permission?)"
else
  echo "docker is not installed yet."
fi
echo

echo "=== Listeners on ports 80/443 (whatever's already using them) ==="
ss -ltnp 2>/dev/null | grep -E ':80 |:443 ' || echo "(nothing listening on 80/443, or ss unavailable)"
echo

echo "=== Ports this stack wants to use on 127.0.0.1 (from $ENV_FILE if present) ==="
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi
for p in "${BACKEND_PORT:-8100}" "${OPERATOR_PORT:-3100}" "${PLATFORM_PORT:-3101}" "${WIDGET_PORT:-8180}"; do
  if ss -ltn 2>/dev/null | grep -q ":$p "; then
    echo "  ✗ port $p is ALREADY in use — pick a different value for the corresponding *_PORT variable in $ENV_FILE"
  else
    echo "  ✓ port $p is free"
  fi
done
echo

echo "=== Existing Nginx sites (sites-enabled) ==="
ls -la /etc/nginx/sites-enabled 2>/dev/null || echo "(Nginx not installed yet, or no sites-enabled directory)"
echo

echo "=== Existing Let's Encrypt certificates ==="
ls /etc/letsencrypt/live 2>/dev/null || echo "(none — certbot not run yet, or not installed)"
echo

echo "=== RastiChat's own persistent paths (created fresh if absent, never touched if they already exist) ==="
for p in "${MEDIA_HOST_PATH:-/opt/rastichat/media}" "${STATIC_HOST_PATH:-/opt/rastichat/staticfiles}" "${BACKUP_DIR:-/opt/rastichat/backups}"; do
  if [ -d "$p" ]; then
    echo "  $p exists ($(du -sh "$p" 2>/dev/null | cut -f1))"
  else
    echo "  $p does not exist yet — will be created on first deploy."
  fi
done
echo

echo "############################################################"
echo "# End of report. Nothing above was changed."
echo "############################################################"
