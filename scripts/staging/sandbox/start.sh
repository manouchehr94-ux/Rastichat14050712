#!/bin/bash
# sandbox staging stack: 2 daphne workers -> layer-4 balancer -> nginx(TLS) ; next dashboards
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR (state dir with stg/{backend.env,env.staging,ca.crt,...}); see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $S/stg/backend.env; umask 022; L=$S/stg/logs; mkdir -p $L
cd $R/backend
for p in 8101 8102; do nohup $PY -m config.daphne_server -b 127.0.0.1 -p $p config.asgi:application >>$L/daphne-$p.log 2>&1 & echo $! > $L/daphne-$p.pid; done
PORT=8100 nohup node $SB/tcp-balancer.mjs >>$L/balancer.log 2>&1 & echo $! > $L/balancer.pid
cd $S/stg/operator-app; PORT=3100 HOSTNAME=127.0.0.1 nohup node server.js >>$L/operator.log 2>&1 & echo $! > $L/operator.pid
cd $S/stg/platform-app; PORT=3101 HOSTNAME=127.0.0.1 nohup node server.js >>$L/platform.log 2>&1 & echo $! > $L/platform.pid
nginx -t 2>/dev/null && (pgrep nginx >/dev/null && nginx -s reload || nginx)
