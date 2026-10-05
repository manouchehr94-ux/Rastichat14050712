#!/bin/bash
# usage: pw.sh <logfile> <playwright args...>
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR, see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $SB/pw.env; cd $R/e2e
LOG=$1; shift
npx playwright test -c staging-django52/playwright.config.ts "$@" > $LOG 2>&1
echo "exit=$?" >> $LOG
