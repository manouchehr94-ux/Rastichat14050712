#!/bin/bash
# Upgrade-style migration check (spec §37): realistic synthetic EXISTING data created with the PREVIOUS release (origin/main) is migrated
# by the NEW release and must survive intact. Own database (rasti_upg); nothing else is touched. Evidence on stdout.
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $S/stg/backend.env
PREV=$S/stg/prev-release
[ -d $PREV ] || (cd $R && git worktree add --detach $PREV origin/main >/dev/null 2>&1)
(cd $PREV && git checkout -q --detach origin/main) 2>/dev/null
echo "previous release: $(cd $PREV && git rev-parse --short HEAD)   new release: $(cd $R && git rev-parse --short HEAD)"
su postgres -c "dropdb --if-exists rasti_upg"; su postgres -c "createdb -O rasti rasti_upg"
export DATABASE_URL=postgres://rasti:rasti@127.0.0.1/rasti_upg DB_NAME=rasti_upg COUNTS_OUT=$S/stg/upgrade-counts.json
set -eo pipefail
echo "== 1. previous release: migrate + seed synthetic existing state"
(cd $PREV/backend && $PY manage.py migrate --noinput -v0 && $PY manage.py shell < $SB/upgrade-seed.py 2>&1 | tee $S/stg/upgrade-seed.out | tail -2; grep -q "seeded existing state" $S/stg/upgrade-seed.out)
echo "== 2. new release: migrate the SAME database"
(cd $R/backend && $PY manage.py migrate --noinput | tail -8 && $PY manage.py migrate --check && $PY manage.py makemigrations --check --dry-run | tail -1)
echo "== 3. verify: nothing lost, legacy rows untouched, new code works on legacy data"
(cd $R/backend && $PY manage.py shell < $SB/upgrade-verify.py 2>&1 | tee $S/stg/upgrade-verify.out | tail -3; grep -q "UPGRADE VERIFIED" $S/stg/upgrade-verify.out)
echo "== 4. reverse EVERY new migration on the populated database (schema rollback) and re-apply"
(cd $R/backend && $PY manage.py migrate integrations zero --noinput -v0 && $PY manage.py migrate conversations 0006 --noinput -v0 && $PY manage.py migrate notifications 0002 --noinput -v0 \
  && $PY manage.py migrate projects 0003 --noinput -v0 && echo "reversed: integrations, conversations 0007-0008, notifications 0003, projects 0004" \
  && $PY manage.py migrate --noinput -v0 && $PY manage.py migrate --check && echo "re-applied cleanly" && $PY manage.py shell < $SB/upgrade-reverify.py 2>&1 | tee $S/stg/upgrade-reverify.out | tail -2; grep -q "REVERIFIED" $S/stg/upgrade-reverify.out)
su postgres -c "dropdb --if-exists rasti_upg"
