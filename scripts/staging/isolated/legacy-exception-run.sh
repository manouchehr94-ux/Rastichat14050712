#!/usr/bin/env bash
# One-off verification of the time-boxed widget-only legacy exception on the ISOLATED staging stack (never a permanent state):
#   1. window OPEN  (UNTIL = today + 3): gate prints common.I002 and stays green; old widget works; staff JWT-in-URL refused; usage counted
#   2. window OVER  (UNTIL = yesterday): legacy off, gate red with common.W003, new clients fine
#   3. variables removed again: gate green, nothing configured
# The staging environment file is restored afterwards (and on any failure).
set -uo pipefail
: "${STG_DIR:?}" "${SRC:?}"
[ "${DJANGO52_ISOLATED_STAGING:-}" = "yes-this-is-an-isolated-staging-stack" ] || { echo "REFUSING: not confirmed isolated" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK="bash $HERE/stack.sh"
cp "$STG_DIR/env.staging" "$STG_DIR/env.staging.pre-legacy"
restore() { cp "$STG_DIR/env.staging.pre-legacy" "$STG_DIR/env.staging"; }
trap restore EXIT
. "$STG_DIR/test-env.sh"
gate() { ( set -a; . "$STG_DIR/env.staging"; set +a; cd "$SRC/backend" && "$STG_DIR/venv/bin/python" manage.py check --deploy --fail-level WARNING --tag security 2>&1 ); }
fail=0
until_open=$(date -u -d "+3 days" +%F); until_over=$(date -u -d "-1 day" +%F)
apply() { cp "$STG_DIR/env.staging.pre-legacy" "$STG_DIR/env.staging"; [ -n "$1" ] && printf 'LEGACY_URL_CREDENTIALS_ENABLED=1\nLEGACY_URL_CREDENTIALS_ACK=accept-credentials-in-urls\nLEGACY_URL_CREDENTIALS_SCOPE=widget\nLEGACY_URL_CREDENTIALS_UNTIL=%s\n' "$1" >> "$STG_DIR/env.staging"; $STACK restart-backend >/dev/null 2>&1; sleep 3; }

echo "== 1. window OPEN until $until_open (widget only)"
apply "$until_open"
out=$(gate); rc=$?; echo "$out" | grep -E "I002|TEMPORARY" | head -2 | sed 's/^/    /'
[ $rc = 0 ] && echo "  PASS  gate stays green while the owner-approved window is open (rc=0)" || { echo "  FAIL  gate rc=$rc"; fail=1; }
echo "$out" | grep -q "common.I002" && echo "  PASS  the gate still announces the exception (common.I002)" || { echo "  FAIL  no I002 line"; fail=1; }
node "$HERE/../../../e2e/staging-django52/legacy-checks.mjs" window-open || fail=1
echo "  usage report:"; ( set -a; . "$STG_DIR/env.staging"; set +a; cd "$SRC/backend" && "$STG_DIR/venv/bin/python" manage.py report_legacy_credential_usage --days 1 2>&1 | sed 's/^/    /' | head -8 )

echo "== 2. window OVER ($until_over): the variables outlived their date"
apply "$until_over"
out=$(gate); rc=$?
[ $rc != 0 ] && echo "  PASS  gate is red after the window (rc=$rc)" || { echo "  FAIL  gate stayed green"; fail=1; }
echo "$out" | grep -q "common.W003" && echo "  PASS  gate reports common.W003 until the variables are removed" || { echo "  FAIL  no W003 line"; fail=1; }
node "$HERE/../../../e2e/staging-django52/legacy-checks.mjs" window-over || fail=1

echo "== 3. variables removed"
apply ""
out=$(gate); rc=$?
[ $rc = 0 ] && ! echo "$out" | grep -q "I002\|W003" && echo "  PASS  gate green and silent: nothing configured (rc=0)" || { echo "  FAIL  gate rc=$rc"; fail=1; }
node "$HERE/../../../e2e/staging-django52/legacy-checks.mjs" window-over || fail=1
[ $fail = 0 ] && echo "LEGACY EXCEPTION RUN: ALL CHECKS PASSED (environment restored)" || echo "LEGACY EXCEPTION RUN: FAILURES"
exit $fail
