#!/usr/bin/env bash
# Rollback rehearsal on the ISOLATED staging stack only (refuses elsewhere). Two variants, then a roll-forward:
#   A  code-only rollback: previous release's code against the UPGRADED database (what a fast "redeploy the old image" does)
#   B  full rollback: previous release's code + the database restored from the pre-upgrade snapshot
#   C  roll forward again (migrate the new code on the restored database)
# Timings are printed so the runbook can state real numbers.
#   STG_DIR=... SRC=<new release checkout> OLD_SRC=<previous release checkout> OLD_PY=<its python> ./rollback-rehearsal.sh
set -uo pipefail
: "${STG_DIR:?}" "${SRC:?}" "${OLD_SRC:?}" "${OLD_PY:?}"
[ "${DJANGO52_ISOLATED_STAGING:-}" = "yes-this-is-an-isolated-staging-stack" ] || { echo "REFUSING: not confirmed isolated" >&2; exit 2; }
[ -e /opt/rastisi-next ] || [ -e /opt/rastichat ] && { echo "REFUSING: looks like the live host" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK="bash $HERE/stack.sh"
set -a; . "$STG_DIR/env.staging"; set +a
CA="$STG_DIR/certs/ca.pem"; B="https://$BACKEND_DOMAIN"
PGE="env PGPASSWORD=$DB_PASSWORD_PLAIN"; PGA="-h 127.0.0.1 -p ${PGPORT:-55432} -U rastichat_stg"
RESULTS="$STG_DIR/rollback-results.txt"; : > "$RESULTS"
say() { echo "$*" | tee -a "$RESULTS"; }
now() { date +%s.%N; }
elapsed() { python3 -c "print(round($(now) - $1, 1))"; }
fail=0; check() { if eval "$2" >/dev/null 2>&1; then say "  PASS  $1"; else say "  FAIL  $1"; fail=1; fi; }
code() { curl -s --noproxy '*' --cacert "$CA" -o /dev/null -w '%{http_code}' "$@"; }
jq_() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
stop_workers() { # SIGTERM, then SIGKILL after 10 s (a Daphne with open sockets never exits on SIGTERM alone)
  local pids=() f; for f in "$STG_DIR"/run/daphne*.pid; do [ -f "$f" ] && pids+=("$(cat "$f")"); rm -f "$f"; done
  [ ${#pids[@]} -gt 0 ] || return 0
  kill "${pids[@]}" 2>/dev/null; sleep 6; kill -9 "${pids[@]}" 2>/dev/null; sleep 1; true; }
start_workers() { # $1 = backend dir, $2 = python
  for i in 1 2; do ( cd "$1" && setsid nohup "$2" -m daphne -b 127.0.0.1 -p $((8100+i)) --application-close-timeout 10 config.asgi:application >> "$STG_DIR/logs/daphne$i.log" 2>&1 & echo $! > "$STG_DIR/run/daphne$i.pid" ); done
  for _ in $(seq 1 40); do [ "$(code $B/api/v1/health/live/)" = 200 ] && break; sleep 1; done
}
smoke_old_widget() { # what an already-published (old) widget does against the rolled-back backend
  local init start tok conv
  init=$(curl -s --noproxy '*' --cacert "$CA" -X POST -H 'Content-Type: application/json' -H "Origin: https://embed-allowed.example.test" -d "{\"project_key\":\"$PROJECT\"}" $B/api/v1/widget/init/)
  tok=$(echo "$init" | jq_ "['session_token']") || return 1
  start=$(curl -s --noproxy '*' --cacert "$CA" -X POST -H 'Content-Type: application/json' -H "Origin: https://embed-allowed.example.test" -d "{\"session_token\":\"$tok\"}" $B/api/v1/widget/start/)
  conv=$(echo "$start" | jq_ "['id']") || return 1
  [ "$(code -H 'Origin: https://embed-allowed.example.test' "$B/api/v1/widget/conversations/$conv/messages/?session_token=$tok")" = 200 ] || return 1
  # old-style socket: credential in the URL path
  ( cd "$HERE/../../../e2e" && node -e "
    const WebSocket=require('ws');const ws=new WebSocket('wss://$BACKEND_DOMAIN/ws/widget/$tok/$conv/',{headers:{Origin:'https://embed-allowed.example.test'}});
    ws.on('open',()=>{ws.send(JSON.stringify({message:'rollback-check',client_message_id:'rb-'+Date.now()}))});
    ws.on('message',m=>{if(String(m).includes('rollback-check')){console.log('ok');process.exit(0)}});
    ws.on('error',e=>{console.log('err',e.message);process.exit(1)});setTimeout(()=>process.exit(2),8000)" | grep -q ok )
}
PROJECT=$(python3 -c "import json;print(json.load(open('$STG_DIR/seed-extra.json'))['A']['project_key'])")
OPUSER=$(python3 -c "import json;d=json.load(open('$STG_DIR/seed-extra.json'))['users']['agent-a1'];print(d['email'])")
OPPASS=$(python3 -c "import json;d=json.load(open('$STG_DIR/seed-extra.json'))['users']['agent-a1'];print(d['password'])")
operator_login_ok() { [ "$(code -X POST -H 'Content-Type: application/json' -d "{\"email\":\"$OPUSER\",\"password\":\"$OPPASS\"}" $B/api/v1/auth/login/)" = 200 ]; }

say "== baseline (new release)"; check "health ready" '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
say "== snapshot of the upgraded database"; t=$(now); $PGE pg_dump $PGA -Fc rastichat_stg > "$STG_DIR/backups/post-upgrade.dump"; say "  pg_dump (upgraded schema): $(elapsed $t)s, $(du -h "$STG_DIR/backups/post-upgrade.dump" | cut -f1)"

say "== A. code-only rollback (old code, UPGRADED database)"
t=$(now); stop_workers; start_workers "$OLD_SRC/backend" "$OLD_PY"; say "  swap to the old release: $(elapsed $t)s"
check "A: health ready"                                  '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
check "A: operator login"                                operator_login_ok
check "A: an old widget can init, start, read history and chat over its legacy socket on the upgraded schema" smoke_old_widget
say "  rows written by the OLD code on the upgraded schema (expires_at null = new column left empty):"
$PGE psql $PGA -d rastichat_stg -Atc "select count(*), count(expires_at), count(hard_expires_at) from visitors_visitorsession where created_at > now() - interval '5 minutes'" | tee -a "$RESULTS"

say "== B. full rollback (old code, database restored from the pre-upgrade snapshot)"
t=$(now); stop_workers
$PGE psql $PGA -d postgres -qc "drop database rastichat_stg with (force)" && $PGE createdb $PGA rastichat_stg
$PGE pg_restore $PGA -d rastichat_stg --no-owner "$STG_DIR/backups/pre-upgrade-oldrelease.dump" 2>>"$RESULTS"
start_workers "$OLD_SRC/backend" "$OLD_PY"; say "  restore + start: $(elapsed $t)s"
check "B: health ready"                                  '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
check "B: the pre-upgrade conversations are all back"    '[ "$($PGE psql $PGA -d rastichat_stg -Atc "select count(*) from conversations_conversation where workspace_id in (select id from workspaces_workspace where name='"'"'OLD-REL Workspace'"'"')")" = 4 ]'
check "B: schema is the old one (no lifecycle column)"   '[ "$($PGE psql $PGA -d rastichat_stg -Atc "select count(*) from information_schema.columns where table_name='"'"'visitors_visitorsession'"'"' and column_name='"'"'expires_at'"'"'")" = 0 ]'

say "== C. roll forward (new code on the restored database)"
t=$(now); stop_workers
( cd "$SRC/backend" && "$STG_DIR/venv/bin/python" manage.py migrate --noinput 2>&1 | tail -3 | tee -a "$RESULTS" )
start_workers "$SRC/backend" "$STG_DIR/venv/bin/python"; say "  migrate + start: $(elapsed $t)s"
check "C: health ready"                                  '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
check "C: legacy sessions were backfilled again"         '[ "$($PGE psql $PGA -d rastichat_stg -Atc "select count(*) from visitors_visitorsession where expires_at is null")" = 0 ]'
say "== seed data lost with the snapshot is recreated (staging only)"; $STACK seed >/dev/null 2>&1; $STACK embed >/dev/null 2>&1; $STACK test-env >/dev/null 2>&1; say "  re-seeded"
[ $fail = 0 ] && say "ROLLBACK REHEARSAL: ALL CHECKS PASSED" || say "ROLLBACK REHEARSAL: FAILURES (see FAIL lines)"
exit $fail
