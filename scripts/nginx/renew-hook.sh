#!/bin/bash
# Certbot deploy-hook: installed into /etc/letsencrypt/renewal-hooks/deploy/
# by scripts/nginx/issue-certs.sh, where certbot's own renewal
# systemd-timer/cron job runs it automatically after ANY successful
# certificate renewal (for any domain, not just RastiChat's — harmless
# either way, a reload is a no-op if nothing changed for this VPS's other
# site's certs).
set -euo pipefail
if nginx -t; then
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl reload nginx
  else
    nginx -s reload
  fi
  echo "$(date -Iseconds) rastichat renew-hook: nginx reloaded after certificate renewal."
else
  echo "$(date -Iseconds) rastichat renew-hook: nginx -t FAILED after renewal — NOT reloading, investigate immediately." >&2
  exit 1
fi
