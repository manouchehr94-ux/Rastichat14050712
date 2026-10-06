#!/bin/bash
# Full verification matrix of the COMBINED integration-platform build (A+B+C+D+E+H, RastiSi adapter PR as a separate checkout) on the isolated
# sandbox staging stack (this container only:
# local nginx + TLS on example.test names, own PostgreSQL/Redis, 2 Daphne workers behind a layer-4 balancer, Next dashboards).
# Nothing here reaches any real host: no VPS, no chatchat.rastisi.ir, no production DB/nginx, no RastiSi.
# usage: run-matrix.sh <run-label> [soak-minutes]      evidence -> $SANDBOX_DIR/evidence/final/<run-label>/
SB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; S=${SANDBOX_DIR:?set SANDBOX_DIR, see README.md}; R=$(cd "$SB/../../.." && pwd); PY=${VENV:-/tmp/venv}/bin/python
RUN=${1:?run label}; SOAK=${2:-30}; E=$S/evidence/final/$RUN; mkdir -p $E
H=$SB; L=$S/stg/logs
declare -a NAMES RCS DURS
ms() { echo $(( $(date +%s%N) / 1000000 )); }
stage() { # stage <name> <command...>   (command runs in a subshell; its output goes to $E/<name>.log)
  local name=$1; shift; local t=$(ms)
  echo "$(date -u +%T) >> $name" | tee -a $E/progress.log
  ( "$@" ) > $E/$name.log 2>&1 < /dev/null; local rc=$?
  NAMES+=("$name"); RCS+=($rc); DURS+=($(( ($(ms) - t) / 1000 )))
  echo "$(date -u +%T) << $name rc=$rc ($(( ($(ms) - t) / 1000 ))s)" | tee -a $E/progress.log
}
restart_stack() { $SB/stop.sh; sleep 2; $SB/start.sh; for i in $(seq 1 60); do [ "$(curl -s --noproxy '*' -o /dev/null -w %{http_code} https://chat-stg.example.test/api/v1/health/)" = 200 ] && break; sleep 1; done
  PID=$(ss -ltnp | grep ":8100 " | grep -o "pid=[0-9]*" | cut -d= -f2); echo $PID > $L/balancer.pid; }
reset_runtime() { redis-cli -n 5 flushdb >/dev/null; for f in $L/daphne-8101.log $L/daphne-8102.log /var/log/nginx/rastichat-*-access.log /var/log/nginx/rastichat-*-error.log /var/log/nginx/error.log /var/log/nginx/access.log; do : > $f; done; }

{
  echo "run: $RUN  started $(date -u +%FT%TZ)"
  echo "tested tree: $(cd $R && git rev-parse HEAD)  (tree $(cd $R && git rev-parse HEAD^{tree}))  branch $(cd $R && git branch --show-current)"
  [ -n "${RASTISI_DIR:-}" ] && echo "RastiSi checkout under test: $(cd $RASTISI_DIR && git rev-parse HEAD)"
  echo "base (main): $(cd $R && git rev-parse --short origin/main)"
  echo "node $(node -v), python $($PY -V 2>&1), nginx $(nginx -v 2>&1 | cut -d/ -f2), postgres $(psql --version | awk '{print $3}'), redis $(redis-server -v | awk '{print $3}' | cut -d= -f2)"
  echo "playwright $(cd $R/e2e && npx playwright --version), chromium $(/opt/pw-browsers/chromium-1194/chrome-linux/chrome --version 2>/dev/null)"
} > $E/environment.txt

# ---------------------------------------------------------------- 0 preflight / migrations
stage 00-preflight bash -c ". $S/stg/backend.env; export DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack DB_HOST=127.0.0.1 PYTHON=$PY PATH=${VENV:-/tmp/venv}/bin:\$PATH; cd $R && bash scripts/staging/django52-preflight.sh"
stage 01-migrate-empty-db bash -c ". $S/stg/backend.env; su postgres -c 'dropdb --if-exists rasti_fresh'; su postgres -c 'createdb -O rasti rasti_fresh'; cd $R/backend && DATABASE_URL=postgres://rasti:rasti@127.0.0.1/rasti_fresh $PY manage.py migrate --noinput | tail -5 && DATABASE_URL=postgres://rasti:rasti@127.0.0.1/rasti_fresh $PY manage.py migrate --check; rc=\$?; su postgres -c 'dropdb --if-exists rasti_fresh'; exit \$rc"

stage 01b-migrate-upgrade-existing-data bash $SB/upgrade-migration.sh

# ---------------------------------------------------------------- unit / integration
stage 02-backend-tests bash -c "export DATABASE_URL=postgres://rasti:rasti@127.0.0.1/rasti REDIS_URL=redis://127.0.0.1:6379/0 DJANGO_SECRET_KEY=test-secret-key-for-local-only-xxxxxxxxxxxxxxxxxxxxxxxx; unset ENVIRONMENT DJANGO_SETTINGS_MODULE MONITORING_TOKEN; cd $R/backend && $PY manage.py test --parallel 4 --noinput"
stage 02b-security-checks bash -c "set -e; cd $R/backend; $PY manage.py makemigrations --check --dry-run | tail -1; \$(dirname $PY)/bandit -r . -x './*/tests*.py,./*/migrations/*,./venv' -ll -q && echo 'bandit: clean'; \$(dirname $PY)/pip-audit -r requirements.txt | tail -2; cd $R; if command -v gitleaks >/dev/null; then gitleaks detect --source . --config .gitleaks.toml --no-banner --redact -v --log-opts 'origin/main..HEAD' && echo 'gitleaks: clean'; else echo 'gitleaks not installed: regex scan of the stack diff'; ! git diff origin/main...HEAD | grep -E '^\\+' | grep -nE 'BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|xox[bp]-' && echo 'regex secret scan: clean'; fi"
stage 03-js-unit-lint-typecheck-build bash -c "set -e; for d in packages/widget apps/operator-dashboard apps/platform-dashboard; do echo \"=== \$d\"; (cd $R/\$d && npx vitest run 2>&1 | tail -6 && npx tsc --noEmit && echo 'typecheck ok' && if [ \$d = packages/widget ]; then npm run lint 2>&1 | tail -2; else npm run lint:baseline 2>&1 | tail -2; fi); done; for d in apps/operator-dashboard apps/platform-dashboard; do (cd $R/\$d && npx next build >/dev/null 2>&1 && echo \"build ok: \$d\"); done; (cd $R && git checkout -q -- apps/operator-dashboard/AGENTS.md apps/platform-dashboard/AGENTS.md 2>/dev/null; true)"
stage 03b-build-widget bash -c "cd $R/packages/widget && npx vite build && cp dist/widget.iife.js /srv/stg/widgetroot/widget.js && ls -l /srv/stg/widgetroot/widget.js"
stage 04-nginx-media-routing bash $R/scripts/nginx/test-media-routing.sh
stage 05-nginx-log-redaction bash $R/scripts/nginx/test-log-redaction.sh

# ---------------------------------------------------------------- real stack: fresh start, clean logs
restart_stack; reset_runtime; restart_stack
stage 06-stack-health bash -c "for u in https://chat-stg.example.test/api/v1/health/ https://operator-stg.example.test/admin/login https://platform-stg.example.test/platform/login https://chat-stg.example.test/widget.js; do printf '%s ' \$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' \$u); echo \$u; done | tee /dev/stderr | grep -vc '^200 ' | grep -qx 0"
# the REAL limiter is proven first (30 parallel bad logins -> 503s). The functional browser suite then runs with ONLY the login zone relaxed
# (10r/m -> 600r/m): it legitimately performs >10 logins per minute from one address, which the production limit rightly refuses.
stage 06b-login-rate-limit-enforced bash $H/loginlimit.sh
relax_login_limit() { sed -i 's#zone=rastichat_login:10m rate=10r/m#zone=rastichat_login:10m rate=600r/m#' /etc/nginx/conf.d/rastichat-limits.conf; nginx -s stop; sleep 1; nginx; sleep 1; }
real_login_limit() { cp $R/deploy/nginx/conf.d/rastichat-limits.conf /etc/nginx/conf.d/rastichat-limits.conf; nginx -s stop; sleep 1; nginx; sleep 1; }
relax_login_limit; echo "login zone relaxed for the functional suite: $(grep -o 'zone=rastichat_login[^;]*' /etc/nginx/conf.d/rastichat-limits.conf)" >> $E/progress.log
reset_runtime   # logs the review stage reads start here (after the stack is healthy, so restart noise is not counted)
stage 07-playwright-all-projects bash -c "PLAYWRIGHT_JSON_OUTPUT_NAME=$E/playwright-results.json $SB/pw.sh $E/playwright-raw.log --retries=0 --reporter=list,json --timeout=90000; cat $E/playwright-raw.log | tail -60; tail -1 $E/playwright-raw.log | grep -qx 'exit=0'"
stage 07b-integration-contract-staging bash -c ". $SB/pw.env; cd $R/e2e && npx playwright test -c integration-staging/playwright.config.ts --reporter=list 2>&1 | tail -40; test \${PIPESTATUS[0]} -eq 0"
sleep 65   # let the nginx WebSocket-connect limiter refill before the next browser suites
stage 07c-reference-host-e2e-on-staging bash -c ". $SB/pw.env; export STACK=staging E2E_PYTHON=$PY PW_CHROMIUM=\$PW_CHROMIUM_PATH; cd $R && bash e2e/reference-host/run.sh --reporter=list 2>&1 | tail -40; test \${PIPESTATUS[0]} -eq 0"
sleep 65
stage 07d-rastisi-cross-system-e2e-on-staging bash -c ". $SB/pw.env; export STACK=staging E2E_PYTHON=$PY SI_PYTHON=${SI_PYTHON:?set SI_PYTHON (RastiSi venv python)} RASTISI_DIR=${RASTISI_DIR:?set RASTISI_DIR (RastiSi checkout at the PR tip)} PW_CHROMIUM=\$PW_CHROMIUM_PATH; cd $R && bash e2e/rastisi/run.sh --reporter=list 2>&1 | tail -40; test \${PIPESTATUS[0]} -eq 0"
real_login_limit; echo "login zone restored to the repo value: $(grep -o 'zone=rastichat_login[^;]*' /etc/nginx/conf.d/rastichat-limits.conf)" >> $E/progress.log

# ---------------------------------------------------------------- soak (alone on the stack; 120 s cool-down so the nginx WebSocket-connect limiter
# (30 r/min, burst 10 per address) is not still charged by the browser suite)
stage 07e-cooldown sleep 120
stage 08-soak bash -c ". $SB/pw.env; cd $H && SOAK_MINUTES=$SOAK SOAK_OUT=$E/soak.json node soak.mjs"

# ---------------------------------------------------------------- logs of everything above
stage 09-log-review bash $H/logcheck.sh

# ---------------------------------------------------------------- rollback rehearsal, then back on the new release
stage 10-rollback-rehearsal bash -c "cd $R && bash $H/rollback.sh"
stage 11-final-health bash -c "curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' https://chat-stg.example.test/api/v1/health/ | grep -qx 200 && cd $R && git status --short | head -20 && echo 'git HEAD:' \$(git rev-parse --short HEAD)"

# ---------------------------------------------------------------- summary
{
  echo "# matrix $RUN  ($(date -u +%FT%TZ))"; cat $E/environment.txt; echo
  printf '| stage | result | seconds |\n|---|---|---|\n'
  bad=0
  for i in "${!NAMES[@]}"; do r=PASS; [ "${RCS[$i]}" -ne 0 ] && { r=FAIL; bad=$((bad+1)); }; printf '| %s | %s | %s |\n' "${NAMES[$i]}" "$r" "${DURS[$i]}"; done
  echo; echo "RESULT: $([ $bad -eq 0 ] && echo GREEN || echo "RED ($bad failing stage(s))")"
} > $E/SUMMARY.md
cat $E/SUMMARY.md
grep -q "RESULT: GREEN" $E/SUMMARY.md
