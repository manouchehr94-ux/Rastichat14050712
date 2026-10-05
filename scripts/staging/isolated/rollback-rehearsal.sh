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
CA="$STG_DIR/certs/ca.pem"; B="https://$BACKEND_DOMAIN"; export NODE_EXTRA_CA_CERTS="$CA"
PGE="env PGPASSWORD=$DB_PASSWORD_PLAIN"; PGA="-h 127.0.0.1 -p ${PGPORT:-55432} -U rastichat_stg"
RESULTS="$STG_DIR/rollback-results.txt"; : > "$RESULTS"
say() { echo "$*" | tee -a "$RESULTS"; }
now() { date +%s.%N; }
elapsed() { python3 -c "print(round($(now) - $1, 1))"; }
fail=0; check() { local out; if out=$(eval "$2" 2>&1); then say "  PASS  $1"; else say "  FAIL  $1"; [ -n "$out" ] && say "        detail: $(echo "$out" | tail -3 | tr '\n' ' ' | cut -c1-300)"; fail=1; fi; }
code() { curl -s --noproxy '*' --cacert "$CA" -o /dev/null -w '%{http_code}' "$@"; }
jq_() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
kill_port() { local pid; pid=$(ss -ltnp "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2); [ -n "$pid" ] && kill -9 "$pid" 2>/dev/null; true; }
stop_workers() { # SIGTERM, then SIGKILL after a grace period (a Daphne with open sockets never exits on SIGTERM alone); finally free the ports
  local pids=() f; for f in "$STG_DIR"/run/daphne*.pid; do [ -f "$f" ] && pids+=("$(cat "$f")"); rm -f "$f"; done
  [ ${#pids[@]} -gt 0 ] && { kill "${pids[@]}" 2>/dev/null; sleep 6; kill -9 "${pids[@]}" 2>/dev/null; }
  kill_port 8101; kill_port 8102; sleep 1; true; }
start_workers() { # $1 = backend dir, $2 = python. The pid recorded is the Daphne process itself (no wrapper subshell)
  local i; pushd "$1" >/dev/null
  for i in 1 2; do setsid nohup "$2" -m daphne -b 127.0.0.1 -p $((8100+i)) --application-close-timeout 10 config.asgi:application >> "$STG_DIR/logs/daphne$i.log" 2>&1 & echo $! > "$STG_DIR/run/daphne$i.pid"; done
  popd >/dev/null
  for _ in $(seq 1 40); do [ "$(code $B/api/v1/health/live/)" = 200 ] && break; sleep 1; done; }
running_release() { # which code answers on 8101: prints "old" or "new" by asking its Django version through the process' cwd
  local pid; pid=$(cat "$STG_DIR/run/daphne1.pid" 2>/dev/null); readlink "/proc/$pid/cwd" 2>/dev/null; }
smoke_old_widget() { # what an already-published (old) widget does against the rolled-back backend
  local init start tok conv h
  init=$(curl -s --noproxy '*' --cacert "$CA" -X POST -H 'Content-Type: application/json' -H "Origin: https://embed-allowed.example.test" -d "{\"project_key\":\"$PROJECT\"}" $B/api/v1/widget/init/)
  tok=$(echo "$init" | jq_ "['session_token']") || { echo "init failed: $init"; return 1; }
  start=$(curl -s --noproxy '*' --cacert "$CA" -X POST -H 'Content-Type: application/json' -H "Origin: https://embed-allowed.example.test" -d "{\"session_token\":\"$tok\"}" $B/api/v1/widget/start/)
  conv=$(echo "$start" | jq_ "['id']") || { echo "start failed: $start"; return 1; }
  h=$(code -H 'Origin: https://embed-allowed.example.test' "$B/api/v1/widget/conversations/$conv/messages/?session_token=$tok")
  [ "$h" = 200 ] || { echo "history with ?session_token= -> HTTP $h"; return 1; }
  # old-style socket: credential in the URL path
  ( cd "$HERE/../../../e2e" && node -e "
    const WebSocket=require('ws');const ws=new WebSocket('wss://$BACKEND_DOMAIN/ws/widget/$tok/$conv/',{headers:{Origin:'https://embed-allowed.example.test'}});
    ws.on('open',()=>{ws.send(JSON.stringify({message:'rollback-check',client_message_id:'rb-'+Date.now()}))});
    ws.on('message',m=>{if(String(m).includes('rollback-check')){console.log('ok');process.exit(0)}});
    ws.on('unexpected-response',(q,r)=>{console.log('ws handshake HTTP',r.statusCode);process.exit(3)});ws.on('error',e=>{console.log('err',e.message);process.exit(1)});setTimeout(()=>{console.log('timeout');process.exit(2)},8000)" | tee /dev/stderr | grep -q ok )
}
PROJECT=$(python3 -c "import json;print(json.load(open('$STG_DIR/seed-extra.json'))['A']['project_key'])")
OPUSER=$(python3 -c "import json;d=json.load(open('$STG_DIR/seed-extra.json'))['users']['agent-a1'];print(d['email'])")
OPPASS=$(python3 -c "import json;d=json.load(open('$STG_DIR/seed-extra.json'))['users']['agent-a1'];print(d['password'])")
operator_login_ok() { [ "$(code -X POST -H 'Content-Type: application/json' -d "{\"email\":\"$OPUSER\",\"password\":\"$OPPASS\"}" $B/api/v1/auth/login/)" = 200 ]; }

say "== baseline (new release)"; check "health ready" '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
say "== snapshot of the upgraded database"; t=$(now); $PGE pg_dump $PGA -Fc rastichat_stg > "$STG_DIR/backups/post-upgrade.dump"; say "  pg_dump (upgraded schema): $(elapsed $t)s, $(du -h "$STG_DIR/backups/post-upgrade.dump" | cut -f1)"

# The previous release has no project-aware origin validation (P1-4): a storefront origin is accepted only if it is listed in
# CORS_ALLOWED_ORIGINS, exactly as the old deployment's env file lists it. A rollback therefore needs those origins in that list.
CORS_NEW="$CORS_ALLOWED_ORIGINS"; export CORS_ALLOWED_ORIGINS="$CORS_ALLOWED_ORIGINS,https://embed-allowed.example.test"
say "== A. code-only rollback (old code, UPGRADED database)"
t=$(now); stop_workers; start_workers "$OLD_SRC/backend" "$OLD_PY"; say "  swap to the old release: $(elapsed $t)s"
check "A: the OLD release is the one answering (process cwd is the old checkout)" '[ "$(running_release)" = "$OLD_SRC/backend" ]'
check "A: health ready"                                  '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
check "A: operator login"                                operator_login_ok
check "A: an old widget can init, start, read history and chat over its legacy socket on the upgraded schema" smoke_old_widget
say "  rows written by the OLD code on the upgraded schema (total | with expires_at | with hard_expires_at; the new-column NULLs are treated as legacy by the new code):"
$PGE psql $PGA -d rastichat_stg -Atc "select count(*), count(expires_at), count(hard_expires_at) from visitors_visitorsession where created_at > now() - interval '5 minutes'" | tee -a "$RESULTS"

say "== B. full rollback (old code, database restored from the pre-upgrade snapshot)"
t=$(now); stop_workers
$PGE psql $PGA -d postgres -qc "drop database rastichat_stg with (force)" && $PGE createdb $PGA rastichat_stg
$PGE pg_restore $PGA -d rastichat_stg --no-owner "$STG_DIR/backups/pre-upgrade-oldrelease.dump" 2>>"$RESULTS"
start_workers "$OLD_SRC/backend" "$OLD_PY"; say "  restore + start: $(elapsed $t)s"
check "B: the OLD release is the one answering" '[ "$(running_release)" = "$OLD_SRC/backend" ]'
check "B: health ready"                                  '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
check "B: the pre-upgrade conversations are all back"    '[ "$($PGE psql $PGA -d rastichat_stg -Atc "select count(*) from conversations_conversation where workspace_id in (select id from workspaces_workspace where name='"'"'OLD-REL Workspace'"'"')")" = 4 ]'
check "B: schema is the old one (no hard_expires_at lifecycle column)"   '[ "$($PGE psql $PGA -d rastichat_stg -Atc "select count(*) from information_schema.columns where table_name='"'"'visitors_visitorsession'"'"' and column_name='"'"'hard_expires_at'"'"'")" = 0 ]'

export CORS_ALLOWED_ORIGINS="$CORS_NEW"
say "== C. roll forward (new code on the restored database)"
t=$(now); stop_workers
( cd "$SRC/backend" && "$STG_DIR/venv/bin/python" manage.py migrate --noinput 2>&1 | tail -3 | tee -a "$RESULTS" )
start_workers "$SRC/backend" "$STG_DIR/venv/bin/python"; say "  migrate + start: $(elapsed $t)s"
check "C: the NEW release is the one answering" '[ "$(running_release)" = "$SRC/backend" ]'
check "C: health ready"                                  '[ "$(code $B/api/v1/health/ready/)" = 200 ]'
check "C: legacy sessions were backfilled again"         '[ "$($PGE psql $PGA -d rastichat_stg -Atc "select count(*) from visitors_visitorsession where expires_at is null")" = 0 ]'
say "== seed data lost with the snapshot is recreated (staging only)"; $STACK seed >/dev/null 2>&1; $STACK embed >/dev/null 2>&1; $STACK test-env >/dev/null 2>&1; say "  re-seeded"
[ $fail = 0 ] && say "ROLLBACK REHEARSAL: ALL CHECKS PASSED" || say "ROLLBACK REHEARSAL: FAILURES (see FAIL lines)"
exit $fail
