#!/bin/bash
L=${SANDBOX_DIR:?}/stg/logs
for p in 3100 3101 8100 8101 8102; do fuser -k $p/tcp >/dev/null 2>&1; done
rm -f $L/*.pid; nginx -s stop 2>/dev/null; true
