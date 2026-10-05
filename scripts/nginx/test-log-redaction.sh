#!/usr/bin/env bash
# Proves deploy/nginx/conf.d/rastichat-log-redaction.conf: runs a throw-away nginx on 127.0.0.1 (high port, temp
# prefix, no root, no system config touched), sends requests carrying fake credentials in the legacy URL forms and
# fails if any secret reaches the access log or if v2 / ordinary URLs get mangled.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-$(command -v nginx || true)}"
[ -x "$NGINX_BIN" ] || { echo "nginx binary not found (set NGINX_BIN)" >&2; exit 2; }
PORT="${PORT:-18089}"
TMP="$(mktemp -d)"
trap '[ -f "$TMP/nginx.pid" ] && kill "$(cat "$TMP/nginx.pid")" 2>/dev/null || true; rm -rf "$TMP"' EXIT
mkdir -p "$TMP/logs" "$TMP/conf.d"
cp "$REPO_ROOT/deploy/nginx/conf.d/rastichat-log-redaction.conf" "$TMP/conf.d/"
cat > "$TMP/nginx.conf" <<CONF
pid $TMP/nginx.pid;
error_log $TMP/logs/error.log;
events {}
http {
  include $TMP/conf.d/*.conf;
  access_log $TMP/logs/access.log rastichat_redacted;
  client_body_temp_path $TMP/b; proxy_temp_path $TMP/p; fastcgi_temp_path $TMP/f; uwsgi_temp_path $TMP/u; scgi_temp_path $TMP/s;
  server { listen 127.0.0.1:$PORT; location / { return 200 "ok"; } }
}
CONF
"$NGINX_BIN" -t -c "$TMP/nginx.conf" -e "$TMP/logs/error.log" -p "$TMP/"
"$NGINX_BIN" -c "$TMP/nginx.conf" -e "$TMP/logs/error.log" -p "$TMP/"
sleep 0.5
U="http://127.0.0.1:$PORT"
UUID=3f2b8c1e-4d5a-4e6f-9a7b-0c1d2e3f4a5b
CONV=9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d
SECRET_WIDGET=SECRETWIDGETTOKEN-11111111-2222-3333-4444-555555555555
SECRET_JWT=SECRETJWT-header.SECRETJWT-payload.SECRETJWT-signature
for path in \
  "/ws/widget/$SECRET_WIDGET/$CONV/" \
  "/ws/dashboard/$SECRET_JWT/$CONV/" \
  "/ws/dashboard/support/$SECRET_JWT/$CONV/" \
  "/ws/notifications/$SECRET_JWT/" \
  "/api/v1/widget/conversations/$CONV/messages/?session_token=$SECRET_WIDGET&x=1" \
  "/media/attachments/2026/10/05/SECRETFILE0123456789abcdef.jpg" "/media/kb_attachments/2026/10/05/SECRETKBFILE.pdf" \
  "/ws/v2/widget/$CONV/" "/ws/v2/dashboard/$CONV/" "/ws/v2/support/$CONV/" "/ws/v2/notifications/" \
  "/api/v1/health/ready/"; do
  curl -sS -o /dev/null "$U$path"
done
sleep 0.5
LOG="$TMP/logs/access.log"
fail=0
if grep -E 'SECRET' "$LOG" >/dev/null; then echo "FAIL: a credential reached the access log:"; grep SECRET "$LOG"; fail=1; fi
if grep -F 'session_token' "$LOG" >/dev/null; then echo "FAIL: query string was logged"; fail=1; fi
for want in '/ws/widget/[redacted]/'"$CONV"'/' '/ws/dashboard/[redacted]/'"$CONV"'/' '/ws/dashboard/support/[redacted]/'"$CONV"'/' \
            '/ws/notifications/[redacted]/' '/media/attachments/[redacted]' '/media/kb_attachments/[redacted]' "/ws/v2/widget/$CONV/" "/ws/v2/dashboard/$CONV/" "/ws/v2/support/$CONV/" '/ws/v2/notifications/' \
            '/api/v1/widget/conversations/'"$CONV"'/messages/ ' '/api/v1/health/ready/'; do
  grep -F -- "$want" "$LOG" >/dev/null || { echo "FAIL: expected log to contain: $want"; fail=1; }
done
if [ "$fail" -ne 0 ]; then echo "--- access.log ---"; cat "$LOG"; exit 1; fi
echo "OK: credentials and query strings are absent from the access log; v2 and ordinary URLs are intact"
cat "$LOG"
