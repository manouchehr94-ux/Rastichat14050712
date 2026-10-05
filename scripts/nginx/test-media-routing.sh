#!/usr/bin/env bash
# Proves the REAL media locations (deploy/nginx/snippets/media-locations.conf.template + chat-media-open.conf.template) with a
# throw-away nginx on a high loopback port and a fake "Django" upstream that answers with X-Accel-Redirect:
#   * /protected-media/ is internal: a direct request from outside is 404;
#   * chat attachments under /media/attachments/ are NOT public by default (404) and are only public in the explicit compat mode;
#   * public KB attachments stay public; any other /media/ path is 404;
#   * an authorized fetch (upstream answers X-Accel-Redirect) is streamed by nginx with the private headers, and Range works
#     (206 + Content-Range) — what voice-note playback in Safari/Chrome needs;
#   * an upstream refusal (404) is passed through and never turns into a file.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-$(command -v nginx || true)}"
[ -x "$NGINX_BIN" ] || { echo "nginx binary not found (set NGINX_BIN)" >&2; exit 2; }
PORT="${PORT:-18091}"; UPPORT="${UPPORT:-18092}"
TMP="$(mktemp -d)"; MEDIA="$TMP/media"; SNIP="$TMP/snippets"
pids=()
cleanup() { [ -f "$TMP/nginx.pid" ] && kill "$(cat "$TMP/nginx.pid")" 2>/dev/null || true; for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$TMP"; }
trap cleanup EXIT
mkdir -p "$MEDIA/attachments/2026/10/05" "$MEDIA/kb_attachments/2026/10/05" "$SNIP" "$TMP/logs"
head -c 4096 /dev/urandom > "$MEDIA/attachments/2026/10/05/private.webm"
printf 'PUBLIC-KB-FILE' > "$MEDIA/kb_attachments/2026/10/05/kb.txt"
chmod -R a+rX "$TMP"   # mktemp -d is 0700; the nginx worker user (when started as root) must be able to read the fixtures

# fake Django: /api/v1/attachments/ok/ authorizes, /api/v1/attachments/denied/ refuses
cat > "$TMP/upstream.py" <<'PY'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if '/ok/' in self.path:
            self.send_response(200)
            self.send_header('X-Accel-Redirect', '/protected-media/attachments/2026/10/05/private.webm')
            self.send_header('Content-Type', 'audio/webm'); self.send_header('Cache-Control', 'private, no-store')
            self.send_header('Content-Disposition', 'inline'); self.send_header('Content-Length', '0'); self.end_headers()
        else:
            self.send_response(404); self.send_header('Content-Length', '0'); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
PY
python3 "$TMP/upstream.py" "$UPPORT" & pids+=($!)

render() { sed -e "s|__MEDIA_HOST_PATH__|$MEDIA|g" -e "s|__SNIPPETS_DIR__|$SNIP|g" "$1"; }
start_nginx() { # $1 = public|private
  [ -f "$TMP/nginx.pid" ] && { kill "$(cat "$TMP/nginx.pid")"; sleep 0.5; }
  render "$REPO_ROOT/deploy/nginx/snippets/media-locations.conf.template" > "$SNIP/media-locations.conf"
  if [ "$1" = public ]; then render "$REPO_ROOT/deploy/nginx/snippets/chat-media-open.conf.template" > "$SNIP/chat-media-open.conf"; else echo "# closed" > "$SNIP/chat-media-open.conf"; fi
  cat > "$TMP/nginx.conf" <<CONF
pid $TMP/nginx.pid; error_log $TMP/logs/error.log;
events {}
http {
  access_log off; client_body_temp_path $TMP/b; proxy_temp_path $TMP/p; fastcgi_temp_path $TMP/f; uwsgi_temp_path $TMP/u; scgi_temp_path $TMP/s;
  include /etc/nginx/mime.types;
  server { listen 127.0.0.1:$PORT;
    location /api/ { proxy_pass http://127.0.0.1:$UPPORT; }
    include $SNIP/media-locations.conf;
  }
}
CONF
  "$NGINX_BIN" -t -c "$TMP/nginx.conf" -e "$TMP/logs/error.log" -p "$TMP/" 2>&1 | tail -1
  "$NGINX_BIN" -c "$TMP/nginx.conf" -e "$TMP/logs/error.log" -p "$TMP/"; sleep 0.5
}
U="http://127.0.0.1:$PORT"
fail=0
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
expect() { local want="$1" got="$2" what="$3"; if [ "$want" = "$got" ]; then echo "  PASS  $what ($got)"; else echo "  FAIL  $what: wanted $want, got $got"; fail=1; fi; }
has_header() { curl -s -D - -o /dev/null "${@:2}" | tr -d '\r' | grep -iq "^$1" ; }

echo "== default (chat attachments private)"
start_nginx private
expect 404 "$(code $U/protected-media/attachments/2026/10/05/private.webm)" "direct request to the internal location"
expect 404 "$(code $U/media/attachments/2026/10/05/private.webm)"           "chat attachment by file name"
expect 404 "$(code $U/media/anything-else.txt)"                              "any other /media/ path"
expect 200 "$(code $U/media/kb_attachments/2026/10/05/kb.txt)"               "public knowledge-base attachment"
expect 200 "$(code $U/api/v1/attachments/ok/?sig=x)"                         "authorized fetch is streamed by nginx"
expect 404 "$(code $U/api/v1/attachments/denied/?sig=x)"                     "upstream refusal stays a 404"
expect 206 "$(code -H 'Range: bytes=0-99' $U/api/v1/attachments/ok/?sig=x)"  "Range request (voice playback)"
expect 206 "$(code -H 'Range: bytes=4000-' $U/api/v1/attachments/ok/?sig=x)" "open-ended Range (seeking)"
has_header 'content-range: bytes 0-99/4096' -H 'Range: bytes=0-99' "$U/api/v1/attachments/ok/?sig=x" && echo "  PASS  Content-Range is correct" || { echo "  FAIL  Content-Range"; fail=1; }
for h in 'cache-control: private, no-store' 'x-content-type-options: nosniff' 'referrer-policy: no-referrer' 'content-type: audio/webm' 'accept-ranges: bytes'; do
  has_header "$h" "$U/api/v1/attachments/ok/?sig=x" && echo "  PASS  header: $h" || { echo "  FAIL  header missing: $h"; fail=1; }
done
body=$(curl -s -r 0-3 "$U/api/v1/attachments/ok/?sig=x" | wc -c); expect 4 "$body" "Range body length"

echo "== compatibility mode (CHAT_ATTACHMENTS_PUBLIC=1) is the only way the old URLs come back"
start_nginx public
expect 200 "$(code $U/media/attachments/2026/10/05/private.webm)"           "chat attachment by file name (compat mode)"
expect 404 "$(code $U/protected-media/attachments/2026/10/05/private.webm)" "the internal location stays internal"
expect 404 "$(code $U/media/anything-else.txt)"                              "any other /media/ path still 404"

[ "$fail" = 0 ] && echo "OK: media routing behaves as designed" || { echo "FAILED"; exit 1; }
