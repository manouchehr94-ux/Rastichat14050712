#!/usr/bin/env bash
# Verifies, against the REAL nginx access log of the staging stack after a matrix run, that the `rastichat_redacted` format keeps
# credentials, query strings and chat-attachment file names out of the log. Read-only.
set -uo pipefail
: "${STG_DIR:?}"
LOG="${LOG:-/var/log/nginx/rastichat-backend-access.log}"
set -a; . "$STG_DIR/env.staging"; set +a
fail=0
say() { echo "$*"; }
check() { # description, command-that-must-print-0
  local n; n=$(eval "$2" 2>/dev/null | tail -1); n=${n:-0}
  if [ "$n" = 0 ]; then say "  PASS  $1"; else say "  FAIL  $1 ($n occurrences)"; fail=1; fi
}
say "log: $LOG ($(wc -l < "$LOG") lines)"
grep -q "rastichat_redacted" /etc/nginx/sites-enabled/rastichat-backend.conf && say "  PASS  the backend site logs with rastichat_redacted" || { say "  FAIL  the backend site does not use rastichat_redacted"; fail=1; }
check "no JWT-looking token (eyJ….….…)"                       "grep -cE 'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.' '$LOG'"
check "no session_token= (query or body echo)"                "grep -c 'session_token=' '$LOG'"
check "no signed attachment signature (sig=)"                 "grep -c 'sig=' '$LOG'"
check "no query string at all on any request line"            "grep -cE '\"(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) [^ ?\"]*\?' '$LOG'"
check "no visitor session token in a /ws/widget/ path"        "grep -E '/ws/widget/' '$LOG' | grep -vc '/ws/widget/\[redacted\]/'"
check "no credential in a legacy /ws/dashboard|notifications path" "grep -E '/ws/(dashboard|notifications)/' '$LOG' | grep -vE '/ws/(dashboard|dashboard/support|notifications)/\[redacted\]' | grep -vc '/ws/v2/'"
# every real attachment file name stored by this run must be absent
names=$(PGPASSWORD="$DB_PASSWORD_PLAIN" psql -h 127.0.0.1 -p "${PGPORT:-55432}" -U rastichat_stg -d rastichat_stg -Atc "select attachment from conversations_message where attachment is not null and attachment <> ''" 2>/dev/null)
total=0; leaked=0
for n in $names; do total=$((total+1)); base=$(basename "$n"); grep -q "$base" "$LOG" && leaked=$((leaked+1)); done
if [ "$leaked" = 0 ]; then say "  PASS  none of the $total stored attachment file names appears in the log"; else say "  FAIL  $leaked of $total attachment file names appear in the log"; fail=1; fi
say "  evidence (redacted lines actually written):"
grep -E "/ws/widget/|/ws/v2/widget|/media/attachments|/api/v1/attachments/" "$LOG" | tail -4 | cut -c1-200 | sed 's/^/    /'
[ $fail = 0 ] && say "REDACTION: OK" || say "REDACTION: FAILED"
exit $fail
