#!/bin/sh
# Single entrypoint for the production backend image — the subcommand
# selects what this specific container instance does, so the SAME image
# built once by CI serves as the web process, the migration job, the
# automation-job worker, and the SLA-evaluator worker (deploy/rollback only
# ever has one image tag to reason about, never four).
#
# Deliberately does NOT run migrations automatically from the `web`
# subcommand: with multiple `web` replicas (or a rolling deploy where old
# and new replicas briefly coexist), every replica running
# `migrate` on startup races every other replica's migration lock and can
# apply a schema change out from under a still-running old-code replica.
# Migrations are their own explicit deploy step (see scripts/staging/deploy.sh)
# run exactly once, before any new `web` replica starts.
set -e

case "$1" in
  web)
    shift
    # Daphne is required (not gunicorn/wsgi): this app serves WebSocket
    # traffic (Channels) on the same port as HTTP, which only an ASGI
    # server can do. DAPHNE_WORKERS/bind are fixed here; scale by running
    # more container replicas behind Nginx, not more threads in one process
    # (Daphne is single-process asyncio, like gunicorn's `-k uvicorn.workers`
    # equivalent — horizontal, not thread-pool, scaling is the supported path).
    exec daphne -b 0.0.0.0 -p "${PORT:-8000}" --application-close-timeout "${DAPHNE_CLOSE_TIMEOUT:-10}" config.asgi:application
    ;;
  migrate)
    exec python manage.py migrate --noinput
    ;;
  collectstatic)
    exec python manage.py collectstatic --noinput
    ;;
  check-deploy)
    # --fail-level WARNING is required: `check --deploy` alone treats a
    # weak/short DJANGO_SECRET_KEY (security.W009) as a WARNING, and
    # Django's check command only exits non-zero on ERROR-level findings by
    # default — a weak secret would otherwise be printed but never actually
    # block a deploy.
    #
    # --tag security scopes this to Django's security-tagged checks only —
    # without it, --fail-level WARNING also catches drf_spectacular's
    # unrelated API-schema-generation warnings (present on every run,
    # nothing to do with deploy safety), which would make this step red
    # forever regardless of the actual deployment's security posture.
    # config/settings.py's SILENCED_SYSTEM_CHECKS additionally silences
    # security.W021 (HSTS preload), a deliberate opt-in the project
    # already accounts for, not something a deploy should ever block on.
    exec python manage.py check --deploy --fail-level WARNING --tag security
    ;;
  test)
    shift
    exec python manage.py test "$@"
    ;;
  automation-worker)
    # Safe to run as N parallel replicas or on a schedule: each due job is
    # claimed with SELECT ... FOR UPDATE SKIP LOCKED (see
    # automations/management/commands/process_automation_jobs.py), so two
    # overlapping invocations can never process the same job twice.
    exec python manage.py process_automation_jobs "$@"
    ;;
  sla-worker)
    # Same SELECT ... FOR UPDATE overlap safety as automation-worker, see
    # sla/management/commands/evaluate_sla.py.
    exec python manage.py evaluate_sla "$@"
    ;;
  seed-staging)
    exec python manage.py seed_staging_data "$@"
    ;;
  manage)
    shift
    exec python manage.py "$@"
    ;;
  *)
    # Fall through for anything else (a shell, a one-off management
    # command via `docker compose run backend manage.py shell`, etc.).
    exec "$@"
    ;;
esac
