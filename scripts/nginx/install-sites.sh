#!/bin/bash
# Installs (or updates) ONLY RastiChat's own Nginx sites. Never touches
# /etc/nginx/nginx.conf, never removes or disables any site this script
# didn't itself create, and never reloads Nginx if the resulting config
# fails `nginx -t` — safe to run on a VPS that already serves another site.
#
# Usage: sudo scripts/nginx/install-sites.sh [path-to-env-file]
#   (defaults to .env.staging in the repo root)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-$REPO_ROOT/.env.staging}"
NGINX_SITES_AVAILABLE="/etc/nginx/sites-available"
NGINX_SITES_ENABLED="/etc/nginx/sites-enabled"
RASTICHAT_SNIPPETS_DIR="/etc/nginx/rastichat/snippets"
ACME_WEBROOT="/var/www/rastichat-acme"

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must be run as root (it writes to /etc/nginx). Try: sudo $0 $*" >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Env file not found: $ENV_FILE" >&2
  echo "Copy .env.staging.example (or .env.production.example) to that path and fill it in first." >&2
  exit 1
fi

echo "=== Preflight: existing Nginx state (read-only, nothing below is changed) ==="
echo "--- sites-enabled ---"
ls -la "$NGINX_SITES_ENABLED" 2>/dev/null || echo "(none)"
echo "--- listeners on 80/443 ---"
ss -ltnp 2>/dev/null | grep -E ':80 |:443 ' || echo "(nothing currently listening — fine on a fresh host)"
echo "--- existing Let's Encrypt certs ---"
ls /etc/letsencrypt/live 2>/dev/null || echo "(none)"
echo "--- disk / memory ---"
df -h / 2>/dev/null || true
free -h 2>/dev/null || true
echo "==============================================================="
echo

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${BACKEND_DOMAIN:?BACKEND_DOMAIN must be set in $ENV_FILE (e.g. chat-staging.rastisi.ir)}"
: "${OPERATOR_DOMAIN:?OPERATOR_DOMAIN must be set in $ENV_FILE}"
: "${PLATFORM_DOMAIN:?PLATFORM_DOMAIN must be set in $ENV_FILE}"
BACKEND_PORT="${BACKEND_PORT:-8100}"
OPERATOR_PORT="${OPERATOR_PORT:-3100}"
PLATFORM_PORT="${PLATFORM_PORT:-3101}"
WIDGET_PORT="${WIDGET_PORT:-8180}"
MEDIA_HOST_PATH="${MEDIA_HOST_PATH:-/opt/rastichat/media}"
STATIC_HOST_PATH="${STATIC_HOST_PATH:-/opt/rastichat/staticfiles}"

if ! grep -q "include /etc/nginx/conf.d/\*.conf;" /etc/nginx/nginx.conf 2>/dev/null; then
  echo "WARNING: /etc/nginx/nginx.conf does not include /etc/nginx/conf.d/*.conf — the" >&2
  echo "rate-limit zones and WebSocket upgrade map this script installs there will be" >&2
  echo "silently ignored. This script will NOT edit nginx.conf for you; add that" >&2
  echo "include line to the http{} block yourself, then re-run this script." >&2
fi

mkdir -p "$RASTICHAT_SNIPPETS_DIR" "$ACME_WEBROOT" "$MEDIA_HOST_PATH" "$STATIC_HOST_PATH"
cp "$REPO_ROOT"/deploy/nginx/snippets/*.conf "$RASTICHAT_SNIPPETS_DIR"/
cp "$REPO_ROOT"/deploy/nginx/conf.d/*.conf /etc/nginx/conf.d/
echo "Installed shared snippets to $RASTICHAT_SNIPPETS_DIR and rate-limit/websocket-map to /etc/nginx/conf.d/"

render() {
  local template="$1"
  sed \
    -e "s|__BACKEND_DOMAIN__|${BACKEND_DOMAIN}|g" \
    -e "s|__OPERATOR_DOMAIN__|${OPERATOR_DOMAIN}|g" \
    -e "s|__PLATFORM_DOMAIN__|${PLATFORM_DOMAIN}|g" \
    -e "s|__BACKEND_PORT__|${BACKEND_PORT}|g" \
    -e "s|__OPERATOR_PORT__|${OPERATOR_PORT}|g" \
    -e "s|__PLATFORM_PORT__|${PLATFORM_PORT}|g" \
    -e "s|__WIDGET_PORT__|${WIDGET_PORT}|g" \
    -e "s|__MEDIA_HOST_PATH__|${MEDIA_HOST_PATH}|g" \
    -e "s|__STATIC_HOST_PATH__|${STATIC_HOST_PATH}|g" \
    "$template"
}

# Installs the certificate-free bootstrap config for $domain if (and only
# if) no certificate exists for it yet — a domain that already has one
# always gets the real config below instead, never regressed back to
# bootstrap on a routine re-run.
install_bootstrap_if_needed() {
  local domain="$1" name="$2"
  if [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]; then
    return 1  # certificate exists — caller should install the real config
  fi
  local dest="$NGINX_SITES_AVAILABLE/rastichat-${name}.conf"
  sed -e "s|__DOMAIN__|${domain}|g" "$REPO_ROOT/deploy/nginx/sites/bootstrap.conf.template" > "$dest"
  ln -sf "$dest" "$NGINX_SITES_ENABLED/rastichat-${name}.conf"
  echo "No certificate yet for ${domain} — installed the HTTP-only bootstrap config."
  echo "Run scripts/nginx/issue-certs.sh once this reloads successfully, then re-run this script."
  return 0
}

install_full_site() {
  local template="$1" name="$2"
  local dest="$NGINX_SITES_AVAILABLE/rastichat-${name}.conf"
  render "$template" > "$dest"
  ln -sf "$dest" "$NGINX_SITES_ENABLED/rastichat-${name}.conf"
  echo "Installed $dest -> $NGINX_SITES_ENABLED/rastichat-${name}.conf"
}

for entry in "backend:${BACKEND_DOMAIN}" "operator-dashboard:${OPERATOR_DOMAIN}" "platform-dashboard:${PLATFORM_DOMAIN}"; do
  name="${entry%%:*}"; domain="${entry#*:}"
  if install_bootstrap_if_needed "$domain" "$name"; then
    continue
  fi
  install_full_site "$REPO_ROOT/deploy/nginx/sites/${name}.conf.template" "$name"
done

echo
echo "=== nginx -t ==="
if nginx -t; then
  echo "Config OK. Reloading Nginx..."
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl reload nginx
  else
    nginx -s reload
  fi
  echo "Done. RastiChat's 3 sites are installed and Nginx reloaded."
  echo "NOTE: until certificates exist under /etc/letsencrypt/live/<domain>/, the"
  echo "443 server blocks above will fail to load — run scripts/nginx/issue-certs.sh"
  echo "next (it works over the plain-HTTP :80 blocks this script just installed)."
else
  echo "nginx -t FAILED — the newly installed rastichat-*.conf files were written but" >&2
  echo "NOT reloaded into the running Nginx. Fix the error above, then re-run this script." >&2
  exit 1
fi
