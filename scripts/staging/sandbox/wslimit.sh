#!/bin/bash
# nginx WebSocket-handshake limit (conf.d/rastichat-limits.conf: zone rastichat_ws_connect, 30 r/min per address, burst 10) must be ENFORCED with the
# real config: 40 rapid handshakes from one address -> roughly the first ~11 are passed to the app (any answer but 503), the rest get 503.
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR, see README.md}
. $SB/pw.env
grep -n "rastichat_ws_connect" /etc/nginx/conf.d/rastichat-limits.conf | head -2
CONV=00000000-0000-0000-0000-000000000000
out=$(for i in $(seq 1 40); do (curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' https://chat-stg.example.test/ws/v2/widget/$CONV/ \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H "Origin: $DJANGO52_ALLOWED_EMBED_ORIGIN" &); done; sleep 8)
echo "$out" | sort | uniq -c
n503=$(echo "$out" | grep -c '^503$'); nother=$(echo "$out" | grep -vc '^503$')
echo "503=$n503 passed-through=$nother"
[ "$n503" -ge 15 ] && [ "$nother" -ge 1 ] && echo "PASS handshake burst is limited" || { echo "FAIL handshake burst not limited as configured"; exit 1; }
