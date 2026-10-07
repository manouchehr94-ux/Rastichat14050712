#!/bin/bash
# sandbox staging stack: 2 daphne workers -> layer-4 balancer -> nginx(TLS) ; next dashboards
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR, see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $S/stg/backend.env; umask 022; L=$S/stg/logs; mkdir -p $L
cd $R/backend
for p in 8101 8102; do nohup $PY -m config.daphne_server -b 127.0.0.1 -p $p config.asgi:application >>$L/daphne-$p.log 2>&1 & echo $! > $L/daphne-$p.pid; done
PORT=8100 nohup node $SB/tcp-balancer.mjs >>$L/balancer.log 2>&1 & echo $! > $L/balancer.pid
cd $S/stg/operator-app; PORT=3100 HOSTNAME=127.0.0.1 nohup node server.js >>$L/operator.log 2>&1 & echo $! > $L/operator.pid
cd $S/stg/platform-app; PORT=3101 HOSTNAME=127.0.0.1 nohup node server.js >>$L/platform.log 2>&1 & echo $! > $L/platform.pid
# the widget bundle is served by a tiny static server (nginx proxies /widget.js to WIDGET_PORT)
(cd /srv/stg/widgetroot && nohup python3 -m http.server 8180 --bind 127.0.0.1 >>$L/widget.log 2>&1 & echo $! > $L/widget.pid)
nginx -t 2>/dev/null && (pgrep nginx >/dev/null && nginx -s reload || nginx)
