#!/usr/bin/env bash
# Scan the staging logs for what the owner asked to be watched: Channels / asyncio / event-loop errors (the one-off error seen under
# parallel test suites) plus database-connection exhaustion and unhandled exceptions. Read-only; prints counts and samples.
set -uo pipefail
: "${STG_DIR:?}"
# `scan-logs.sh mark` remembers how many lines each log has now; a later `scan-logs.sh` looks only at what was written since.
LOGS=("$STG_DIR/logs/daphne1.log" "$STG_DIR/logs/daphne2.log" "$STG_DIR/logs/aux-nginx-error.log" /var/log/nginx/rastichat-backend-error.log)
MARKS="$STG_DIR/logs/.marks"
if [ "${1:-}" = mark ]; then : > "$MARKS"; for f in "${LOGS[@]}"; do echo "$f $(wc -l < "$f" 2>/dev/null || echo 0)" >> "$MARKS"; done; echo "marked"; exit 0; fi
since() { local m=0; [ -f "$MARKS" ] && m=$(grep -F "$1 " "$MARKS" | awk '{print $2}' | tail -1); tail -n +$(( ${m:-0} + 1 )) "$1"; }
printf '%-52s %s\n' "pattern" "count (daphne1 + daphne2 + nginx error log)"
tot=0
scan() { # label, regex
  local n=0
  for f in "${LOGS[@]}"; do
    [ -f "$f" ] || continue
    c=$(since "$f" | grep -cE "$2" 2>/dev/null); n=$((n + ${c:-0}))
  done
  printf '%-52s %s\n' "$1" "$n"; echo "$1:$n" >> "$STG_DIR/logs/scan.tmp"
}
: > "$STG_DIR/logs/scan.tmp"
scan "Traceback"                                    'Traceback \(most recent call last\)'
scan "asyncio / event loop errors"                  'Event loop is closed|event loop|no running event loop|RuntimeError: .*loop|Task exception was never retrieved|Task was destroyed but it is pending'
scan "CancelledError reaching the log"              'CancelledError'
scan "SynchronousOnlyOperation"                     'SynchronousOnlyOperation'
scan "database connection exhaustion"               'too many clients|remaining connection slots'
scan "channels_redis / Redis errors"                'ConnectionError|redis\.exceptions|TimeoutError'
scan "daphne 'Exception inside application'"       'Exception inside application'
scan "HTTP 500 responses (daphne access)"           '" 500 '
scan "nginx upstream / limit_req errors"            'upstream timed out|no live upstreams|limiting requests'
echo; echo "samples of anything above (first 3 of each file):"
for f in "$STG_DIR/logs/daphne1.log" "$STG_DIR/logs/daphne2.log"; do
  since "$f" | grep -E 'Traceback|Exception inside application|Event loop|SynchronousOnlyOperation|too many clients' 2>/dev/null | head -3 | cut -c1-220 | sed "s|^|  $(basename $f): |"
done
rm -f "$STG_DIR/logs/scan.tmp"
