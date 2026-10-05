#!/bin/bash
# Rollback rehearsal on the sandbox staging stack ONLY (own DBs rasti_stg / rasti_rb, own local nginx). Prints a timed report.
# 1) snapshot the DB  2) stop the new release, restore the snapshot into rasti_rb, start the PREVIOUS release (origin/main = PR base)
#    and flip nginx to the compat switch  3) verify chat + old-style attachment URL  4) roll forward again, verify private attachments are closed.
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR, see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $SB/pw.env; . $S/stg/backend.env; L=$S/stg/logs
ms() { echo $(( $(date +%s%N) / 1000000 )); }
T0=$(ms); ok=0; bad=0
el() { echo $(( ($(ms) - $1) )); }   # elapsed ms since $1
step() { printf '%s  +%6dms  %s\n' "$(date -u +%T)" "$(el $T0)" "$*"; }
check() { if [ "$2" = "$3" ]; then ok=$((ok+1)); step "PASS $1 ($2)"; else bad=$((bad+1)); step "FAIL $1 (got $2, want $3)"; fi; }
code() { curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "$@"; }
workers() { # $1 = backend dir, $2 = database url
  for p in 8101 8102; do fuser -k $p/tcp >/dev/null 2>&1; done; sleep 1
  for p in 8101 8102; do (cd $1 && DATABASE_URL=$2 nohup $PY -m $( [ -f $1/config/daphne_server.py ] && echo config.daphne_server || echo daphne ) -b 127.0.0.1 -p $p config.asgi:application >>$L/daphne-$p.log 2>&1 & echo $! > $L/daphne-$p.pid); done
  for i in $(seq 1 60); do [ "$(code https://chat-stg.example.test/api/v1/health/)" = 200 ] && return 0; sleep 1; done; return 1; }
install_nginx() { # $1 = CHAT_ATTACHMENTS_PUBLIC
  (cd $R && CHAT_ATTACHMENTS_PUBLIC=$1 bash scripts/nginx/install-sites.sh $S/stg/env.staging </dev/null >/dev/null 2>&1
   sed -i '/listen \[::\]/d' /etc/nginx/sites-available/rastichat-*.conf; rm -f /etc/nginx/sites-enabled/default
   nginx -t >/dev/null 2>&1 && nginx -s reload); sleep 1; }
absurl() { case "$1" in http*) echo "$1";; *) echo "https://chat-stg.example.test$1";; esac; }
field() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(*[d[k] for k in sys.argv[1:]])' "$@"; }

step "start: new release (combined #17+#18) is running"
check "health (new release)" "$(code https://chat-stg.example.test/api/v1/health/)" 200
F1=$(cd $SB && node flow.mjs); step "flow(new): $F1"
URL1=$(echo "$F1" | field attachment_url)
FILE1=$(ls -t /srv/stg/media/attachments/*/*/*/* 2>/dev/null | head -1); NAME1=${FILE1#/srv/stg/media/}; step "attachment stored as $NAME1"
check "new release: signed URL" "$(code $(absurl $URL1))" 200
check "new release: /media/$NAME1 closed" "$(code https://chat-stg.example.test/media/$NAME1)" 404
MSGS=$(su postgres -c "psql -At rasti_stg -c 'select count(*) from conversations_message'")

T=$(ms); su postgres -c "pg_dump -Fc rasti_stg" > $S/stg/snapshot.dump
step "snapshot taken: $(du -h $S/stg/snapshot.dump | cut -f1) in $(el $T)ms, messages=$MSGS"

[ -d $S/stg/prev-release ] || (cd $R && git worktree add --detach $S/stg/prev-release origin/main >/dev/null 2>&1)
PREV=$(cd $S/stg/prev-release && git rev-parse --short HEAD); step "previous release (PR base): $PREV"

T=$(ms); su postgres -c "dropdb --if-exists rasti_rb"; su postgres -c "createdb -O rasti rasti_rb"
su postgres -c "pg_restore -d rasti_rb --no-owner --role=rasti" < $S/stg/snapshot.dump 2>/dev/null
RB=$(su postgres -c "psql -At rasti_rb -c 'select count(*) from conversations_message'"); step "DB restored into rasti_rb in $(el $T)ms, messages=$RB"
check "restored DB has the same message count" "$RB" "$MSGS"

T=$(ms); workers $S/stg/prev-release/backend postgres://rasti:rasti@127.0.0.1/rasti_rb
check "previous release answers health" "$(code https://chat-stg.example.test/api/v1/health/)" 200; step "previous release up in $(el $T)ms"
T=$(ms); install_nginx 1; step "nginx compat switch (CHAT_ATTACHMENTS_PUBLIC=1) applied in $(el $T)ms"
F2=$(cd $SB && node flow.mjs); step "flow(prev): $F2"
check "previous release: customer flow init/start/upload/ticket/ws-auth/echo" "$(echo "$F2" | field start upload ticket authOk echoed)" "200 201 201 True True"
URL2=$(echo "$F2" | field attachment_url)
check "previous release issues a public /media/ URL that nginx serves in compat mode" "$(code $(absurl $URL2))" 200
check "attachment created by the new release readable by its old-style URL in compat mode" "$(code https://chat-stg.example.test/media/$NAME1)" 200
check "/protected-media/ is never reachable directly" "$(code https://chat-stg.example.test/protected-media/$NAME1)" 404

step "roll forward: new release + private attachments again"
T=$(ms); workers $R/backend postgres://rasti:rasti@127.0.0.1/rasti_stg
check "new release health after roll-forward" "$(code https://chat-stg.example.test/api/v1/health/)" 200; step "new release back in $(el $T)ms"
install_nginx 0
check "/media/$NAME1 closed again" "$(code https://chat-stg.example.test/media/$NAME1)" 404
F3=$(cd $SB && node flow.mjs)
check "new release customer flow after roll-forward" "$(echo "$F3" | field start upload ticket authOk echoed)" "200 201 201 True True"
check "signed URL works again" "$(code $(absurl $(echo "$F3" | field attachment_url)))" 200
su postgres -c "dropdb --if-exists rasti_rb"
step "ROLLBACK REHEARSAL: $ok passed, $bad failed, total $(el $T0)ms"
[ $bad -eq 0 ]
