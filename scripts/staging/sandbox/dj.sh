#!/bin/bash
# run python (stdin) inside the sandbox staging Django env
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR (state dir with stg/{backend.env,env.staging,ca.crt,...}); see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $S/stg/backend.env; cd $R/backend; $PY manage.py shell 2>&1 | grep -v "objects imported"
