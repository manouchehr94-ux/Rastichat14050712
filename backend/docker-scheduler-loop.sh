#!/bin/sh
# Runs a management command on a fixed interval forever — the "dedicated
# scheduler container" option from Phase 6 section 10, chosen over systemd
# timers because it keeps the whole staging stack self-contained inside
# `docker compose up` rather than depending on host-level unit files that
# would need separate installation/management outside Compose.
#
# Overlap safety does NOT come from this loop (a naive "don't start the next
# run until the last one finished" loop like this one gives none on its
# own) — it comes from the management commands themselves, which claim each
# unit of work with `SELECT ... FOR UPDATE SKIP LOCKED` (see
# automations/management/commands/process_automation_jobs.py and
# sla/management/commands/evaluate_sla.py). That means it's also safe to run
# this loop as more than one replica, or alongside a manual one-off
# `docker compose run backend manage automation-worker`, without ever
# double-processing the same job.
#
# Usage: docker-scheduler-loop.sh <entrypoint-subcommand> <interval-seconds>
set -eu

SUBCOMMAND="$1"
INTERVAL="${2:-60}"
HEARTBEAT_FILE="${SCHEDULER_HEARTBEAT_FILE:-/tmp/scheduler-heartbeat}"

shutting_down=0
term_handler() {
  echo "[$(date -Iseconds)] Received termination signal, exiting after the current cycle..."
  shutting_down=1
}
trap term_handler TERM INT

echo "[$(date -Iseconds)] Starting scheduler loop: ${SUBCOMMAND} every ${INTERVAL}s"

while [ "$shutting_down" -eq 0 ]; do
  if /app/docker-entrypoint.sh "$SUBCOMMAND"; then
    cycle_status=SUCCESS
  else
    cycle_status=FAILURE
    echo "[$(date -Iseconds)] ${SUBCOMMAND} run failed (see above) — will retry next cycle"
  fi
  date +%s > "$HEARTBEAT_FILE" || true
  # DB-backed heartbeat (in addition to the local file above, which is
  # only what this container's own Docker HEALTHCHECK can see) — lets
  # common.views.SchedulerStatusView answer "when did this scheduler last
  # run" for external monitoring. Failure to record it is logged, never
  # fatal to the loop itself.
  /app/docker-entrypoint.sh manage record_scheduler_heartbeat "$SUBCOMMAND" "$cycle_status" \
    || echo "[$(date -Iseconds)] Failed to record DB heartbeat for ${SUBCOMMAND} (non-fatal)"
  # Backgrounding + wait (rather than a plain `sleep`) so the TERM/INT trap
  # above interrupts the wait immediately instead of only being handled
  # after the full interval elapses.
  sleep "$INTERVAL" &
  wait $! || true
done

echo "[$(date -Iseconds)] Scheduler loop stopped."
