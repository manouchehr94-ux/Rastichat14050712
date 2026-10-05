#!/bin/bash
# Log review for one matrix run: asyncio/event-loop errors in Daphne, credential leakage in nginx/Daphne logs, nginx crit/emerg.
# Reads the logs as they are (the matrix truncates them at the start of the browser stage). Exit 1 on any finding.
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR}; L=$S/stg/logs
bad=0
say() { echo "$*"; }
fail() { bad=$((bad+1)); say "FINDING: $*"; }

say "== Daphne/Django logs ($(cat $L/daphne-8101.log $L/daphne-8102.log | wc -l) lines)"
PAT='Traceback|CRITICAL|\bERROR\b|asyncio|Task exception was never retrieved|Future exception|event loop|Event loop|CancelledError|RuntimeWarning|was never awaited|Exception in callback|too many connections|database is locked|OperationalError|InterfaceError|Application instance .* took too long|Daphne: .* timeout'
EXCL='unable to guess serializer|could not derive type|Error \[[A-Za-z]+\]: .*graceful fallback'
for f in $L/daphne-8101.log $L/daphne-8102.log; do
  n=$(grep -E "$PAT" $f | grep -vE "$EXCL" | wc -l)
  say "$(basename $f): $n matching lines"
  if [ "$n" -gt 0 ]; then fail "$(basename $f) has $n error/asyncio lines"; grep -E "$PAT" $f | grep -vE "$EXCL" | cut -c1-260 | sort | uniq -c | sort -rn | head -8; fi
done
say "(drf-spectacular schema warnings 'unable to guess serializer' are excluded: they are emitted when /schema is rendered, not runtime errors)"

say "== credential leakage in logs"
NG=/var/log/nginx; NLOGS="$NG/rastichat-backend-access.log $NG/rastichat-operator-access.log $NG/rastichat-platform-access.log"; DLOGS="$L/daphne-8101.log $L/daphne-8102.log"
chk() { # $1 label  $2 egrep pattern  $3 logs  $4 gate|warn
  n=$(cat $3 2>/dev/null | grep -aE "$2" | wc -l); say "$1: $n"
  if [ "$n" -gt 0 ]; then
    if [ "$4" = gate ]; then fail "$1 appears $n times in logs"; else say "WARN (non-gating, reported to the owner): $1 appears $n times"; fi
    cat $3 2>/dev/null | grep -aE "$2" | cut -c1-160 | head -2
  fi; }
say "-- nginx access logs (the redaction control; gating)"
chk "attachment signature (sig=)" 'sig=[A-Za-z0-9_:-]{10,}' "$NLOGS" gate
chk "JWT (eyJ...)" 'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.' "$NLOGS" gate
chk "session_token query" 'session_token=' "$NLOGS" gate
chk "ticket in a URL" '[?&]ticket=' "$NLOGS" gate
chk "legacy credential-in-path websocket URLs" '/ws/(widget|dashboard|notifications)/[0-9a-f-]{20,}' "$NLOGS" gate
chk "unredacted attachment file name under /media/attachments/" '/media/(kb_)?attachments/[0-9]{4}/' "$NLOGS" gate
n=$(grep -aE '"(GET|POST|PUT|PATCH|DELETE|HEAD) [^"]*\?[^"]*"' $NG/rastichat-backend-access.log 2>/dev/null | wc -l); say "query strings in backend access log (redacted format logs none): $n"; [ "$n" -gt 0 ] && fail "backend access log contains query strings"
say "redacted markers present (proof the format is active): $(grep -ac '\[redacted\]' $NG/rastichat-backend-access.log 2>/dev/null)"
say "-- Daphne access log (config.daphne_server redacts query strings: any credential here is gating)"
chk "JWT (eyJ...) in Daphne log" 'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.' "$DLOGS" gate
chk "attachment signature (sig=) in Daphne access log" 'sig=[A-Za-z0-9_:-]{10,}' "$DLOGS" gate
chk "session_token query in Daphne access log" 'session_token=' "$DLOGS" gate
chk "ticket in a URL in Daphne access log" '[?&]ticket=' "$DLOGS" gate

say "== nginx"
for f in $NG/error.log $NG/rastichat-backend-error.log $NG/rastichat-operator-error.log $NG/rastichat-platform-error.log; do
  [ -f $f ] || continue
  c=$(grep -cE '\[(emerg|alert|crit)\]' $f); say "$(basename $f): crit/alert/emerg=$c  upstream-errors=$(grep -cE 'upstream (prematurely|timed out)|connect\(\) failed' $f)  limiting=$(grep -c 'limiting' $f)"
done
c=$(cat $NG/rastichat-backend-error.log $NG/rastichat-operator-error.log $NG/rastichat-platform-error.log 2>/dev/null | grep -cE '\[(emerg|alert|crit)\]'); [ "$c" -gt 0 ] && fail "nginx crit/alert/emerg lines: $c"
# upstream errors are expected ONLY inside the outage windows the tests create on purpose (balancer SIGUSR1 .. SIGUSR2, +10 s grace)
c=$(BAL=$L/balancer.log python3 - <<'PY'
import re,datetime as D
L=__import__("os").environ["BAL"]
win=[];down=None
for l in open(L):
    m=re.match(r'(\S+Z) balancer: (DOWN|UP)',l)
    if not m: continue
    t=D.datetime.fromisoformat(m.group(1).replace('Z','+00:00')).replace(tzinfo=None)
    if m.group(2)=='DOWN': down=t
    elif down: win.append((down-D.timedelta(seconds=2),t+D.timedelta(seconds=10))); down=None
n=0
for l in open('/var/log/nginx/rastichat-backend-error.log',errors='ignore'):
    if not re.search(r'upstream (prematurely|timed out)|connect\(\) failed|recv\(\) failed',l): continue
    t=D.datetime.strptime(l[:19],'%Y/%m/%d %H:%M:%S')
    if not any(a<=t<=b for a,b in win): n+=1; print('OUTSIDE-WINDOW:',l[:200],file=__import__('sys').stderr)
print(n)
PY
)
say "nginx upstream errors outside the deliberate outage windows: $c"; [ "$c" -gt 0 ] && fail "nginx upstream errors outside outage windows: $c"
say "status codes in backend access log (since reset): $(awk '{print $9}' $NG/rastichat-backend-access.log 2>/dev/null | sort | uniq -c | sort -rn | tr '\n' ' ')"
say "== result: $bad finding(s)"
[ $bad -eq 0 ]
