#!/bin/bash
# nginx login rate limit (conf.d/rastichat-limits.conf: 10 r/min, burst 5 on /api/v1/auth/login/) must be ENFORCED with the real config:
# a burst of 30 parallel bad-password logins gets 401 for the first few and 503 (nginx limit) for the rest; after the window a good login works.
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR, see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
. $SB/pw.env
grep -n "rastichat_login" /etc/nginx/conf.d/rastichat-limits.conf
out=$(for i in $(seq 1 30); do (curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{"email":"nobody@example.test","password":"wrong"}' https://chat-stg.example.test/api/v1/auth/login/ &) ; done; sleep 6)
echo "$out" | sort | uniq -c
n401=$(echo "$out" | grep -c '^401$'); n503=$(echo "$out" | grep -c '^503$')
echo "401=$n401 503=$n503"
[ "$n503" -ge 10 ] && [ "$n401" -ge 1 ] && echo "PASS burst is limited" || { echo "FAIL burst not limited as configured"; exit 1; }
echo "waiting 75s for the window to drain..."; sleep 75
c=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "{\"email\":\"$SMOKE_OPERATOR_EMAIL\",\"password\":\"$SMOKE_OPERATOR_PASSWORD\"}" https://chat-stg.example.test/api/v1/auth/login/)
echo "good login after the window: $c"; [ "$c" = 200 ] && echo "PASS limiter recovers" || { echo "FAIL"; exit 1; }
