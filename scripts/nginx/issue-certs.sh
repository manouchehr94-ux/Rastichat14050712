#!/bin/bash
# Issues (or renews) Let's Encrypt certificates for RastiChat's 3 domains
# via the webroot method — never the certbot --nginx plugin, which edits
# server blocks on its own and could touch config for the VPS's other
# site. Requires scripts/nginx/install-sites.sh to have already installed
# at least the HTTP-only bootstrap config for each domain (certbot's
# HTTP-01 challenge needs something answering on :80 first).
#
# Usage: sudo scripts/nginx/issue-certs.sh [path-to-env-file]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-$REPO_ROOT/.env.staging}"
ACME_WEBROOT="/var/www/rastichat-acme"

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root. Try: sudo $0 $*" >&2
  exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi
if ! command -v certbot >/dev/null 2>&1; then
  echo "certbot is not installed. On Ubuntu 24.04: sudo apt-get install -y certbot" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
: "${BACKEND_DOMAIN:?BACKEND_DOMAIN must be set in $ENV_FILE}"
: "${OPERATOR_DOMAIN:?OPERATOR_DOMAIN must be set in $ENV_FILE}"
: "${PLATFORM_DOMAIN:?PLATFORM_DOMAIN must be set in $ENV_FILE}"
: "${CERTBOT_EMAIL:?CERTBOT_EMAIL must be set in $ENV_FILE}"

mkdir -p "$ACME_WEBROOT"

# Certbot runs every script under renewal-hooks/deploy/ automatically after
# ANY successful renewal (no per-certificate registration needed) — installed
# here, before the first issuance, so it's already in place for the very
# first automatic renewal.
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
install -m 755 "$REPO_ROOT/scripts/nginx/renew-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/rastichat-reload-nginx.sh

server_public_ip() {
  curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null \
    || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null \
    || true
}

check_dns() {
  local domain="$1" server_ip="$2"
  local resolved
  resolved="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | head -1)"
  if [ -z "$resolved" ]; then
    echo "  ✗ ${domain} does not resolve at all (no A record?)." >&2
    return 1
  fi
  if [ -n "$server_ip" ] && [ "$resolved" != "$server_ip" ]; then
    echo "  ✗ ${domain} resolves to ${resolved}, but this server's public IP looks like ${server_ip}." >&2
    echo "    Fix the DNS A record before requesting a certificate for it (issuing against" >&2
    echo "    the wrong IP will fail the HTTP-01 challenge anyway, but failing fast here is clearer)." >&2
    return 1
  fi
  echo "  ✓ ${domain} resolves to ${resolved}"
  return 0
}

echo "=== DNS verification (no certificate is requested until this passes) ==="
SERVER_IP="$(server_public_ip)"
if [ -z "$SERVER_IP" ]; then
  echo "Could not determine this server's public IP automatically — DNS checks below only" >&2
  echo "confirm each domain resolves to SOME address, not that it's THIS server's." >&2
fi
dns_ok=1
for d in "$BACKEND_DOMAIN" "$OPERATOR_DOMAIN" "$PLATFORM_DOMAIN"; do
  check_dns "$d" "$SERVER_IP" || dns_ok=0
done
if [ "$dns_ok" -ne 1 ]; then
  echo
  echo "One or more domains failed DNS verification — aborting without contacting Let's Encrypt." >&2
  echo "Fix DNS, wait for propagation, then re-run." >&2
  exit 1
fi
echo "=========================================================="
echo

for d in "$BACKEND_DOMAIN" "$OPERATOR_DOMAIN" "$PLATFORM_DOMAIN"; do
  echo "--- Requesting/renewing certificate for ${d} ---"
  certbot certonly \
    --webroot --webroot-path "$ACME_WEBROOT" \
    --non-interactive --agree-tos \
    --email "$CERTBOT_EMAIL" \
    --domains "$d" \
    --keep-until-expiring
done

echo
echo "=== nginx -t + reload (picks up the new certificates) ==="
if nginx -t; then
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl reload nginx
  else
    nginx -s reload
  fi
  echo "Done. Re-run scripts/nginx/install-sites.sh now if any of these domains were still on"
  echo "the HTTP-only bootstrap config — it will switch them to the full HTTPS config now that"
  echo "a certificate exists."
else
  echo "nginx -t failed after certificate issuance — investigate before relying on this." >&2
  exit 1
fi

echo
echo "=== Renewal test (dry run — does not consume rate limit or replace real certs) ==="
certbot renew --dry-run
echo "Renewal dry run OK. Certbot's own systemd timer/cron (installed automatically by the"
echo "certbot package) handles actual periodic renewal; scripts/nginx/renew-hook.sh was"
echo "installed above into /etc/letsencrypt/renewal-hooks/deploy/ and reloads Nginx after"
echo "every real renewal — no further setup needed."
