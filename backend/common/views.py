import hmac
import os
import shutil

import redis as redis_client
from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import SchedulerHeartbeat


def _is_authorized_monitor(request):
    """Gates MonitoringView (always) and adds error detail to Readiness's
    otherwise-public response (see ReadinessView) — a shared-secret header
    rather than session/JWT auth, since the caller is typically an
    infra-side monitoring tool, not a logged-in operator. `hmac.compare_digest`
    avoids leaking the token's correct prefix length via response-time
    differences. In development (no MONITORING_TOKEN configured —
    settings.py requires one in staging/production) any caller is treated
    as authorized, matching this project's existing dev-permissive
    conventions (e.g. CORS_ALLOW_ALL_ORIGINS).
    """
    configured = settings.MONITORING_TOKEN
    if not configured:
        return True
    supplied = request.headers.get('X-Monitoring-Token', '')
    return hmac.compare_digest(supplied, configured)


def _check_database():
    try:
        connections['default'].cursor()
        return True, None
    except Exception as exc:  # noqa: BLE001 - health check must never itself crash
        return False, str(exc)[:200]


def _check_redis():
    """Pings the SAME Redis instance/credentials Channels uses (see
    settings.REDIS_CONNECTION), not Django's cache framework — this project
    has no CACHES backend configured, so a `django.core.cache.cache` probe
    would have silently exercised the in-process LocMemCache and always
    reported "up" even with Redis completely down.
    """
    try:
        conn = redis_client.Redis(
            host=settings.REDIS_CONNECTION['host'],
            port=settings.REDIS_CONNECTION['port'],
            password=settings.REDIS_CONNECTION['password'],
            db=settings.REDIS_CONNECTION['db'],
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        conn.ping()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def _check_migrations():
    try:
        executor = MigrationExecutor(connections['default'])
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return (len(plan) == 0), len(plan)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


class LivenessView(APIView):
    """Answers only "is this process alive and able to handle a request at
    all" — no dependency checks. A container orchestrator restarts the
    process on liveness failure, so this must never fail because of a
    transient DB/Redis outage (that's what readiness is for); it should only
    ever fail if the process itself is wedged.
    """
    permission_classes = []

    def get(self, request):
        return Response({'status': 'alive'})


class ReadinessView(APIView):
    """Answers "can this process correctly serve traffic right now" — used
    to gate whether a load balancer/orchestrator sends it requests. Must
    never report ready when the database or Redis (the Channels/WebSocket
    backend) is unreachable, or when the schema doesn't match the code
    (unapplied migrations) — serving traffic in either state would fail
    requests or corrupt data, not just look nice on a dashboard.

    Stays unauthenticated (Nginx/orchestrators poll this by design, with no
    token) — but raw exception text (connection strings, hostnames, ports)
    is only ever included for a caller presenting a valid monitoring token;
    an anonymous caller only ever sees `up: true/false`, never *why*.
    """
    permission_classes = []

    def get(self, request):
        db_ok, db_error = _check_database()
        redis_ok, redis_error = _check_redis()
        migrations_ok, migrations_detail = _check_migrations()
        show_detail = _is_authorized_monitor(request)

        ready = db_ok and redis_ok and migrations_ok
        body = {
            'status': 'ready' if ready else 'not_ready',
            'components': {
                'database': {'up': db_ok, **({'error': db_error} if db_error and show_detail else {})},
                'redis': {'up': redis_ok, **({'error': redis_error} if redis_error and show_detail else {})},
                'migrations': {
                    'up_to_date': migrations_ok,
                    **(
                        {'pending_count': migrations_detail} if migrations_ok
                        else ({'error': migrations_detail} if show_detail else {})
                    ),
                },
            },
        }
        return Response(body, status=200 if ready else 503)


_EXPECTED_SCHEDULER_INTERVALS = {
    'automation-worker': lambda: settings.AUTOMATION_WORKER_INTERVAL_SECONDS,
    'sla-worker': lambda: settings.SLA_WORKER_INTERVAL_SECONDS,
}


def _scheduler_status():
    now = timezone.now()
    rows = {h.name: h for h in SchedulerHeartbeat.objects.all()}
    result = {}
    for name, interval_fn in _EXPECTED_SCHEDULER_INTERVALS.items():
        row = rows.get(name)
        if row is None:
            result[name] = {'seen': False, 'stale': True}
            continue
        age_seconds = (now - row.last_run_at).total_seconds()
        # Same 5x-interval staleness margin as the scheduler's own Docker
        # HEALTHCHECK (see docker-compose.staging.yml) — kept consistent
        # rather than picking a second, independent threshold here.
        stale = age_seconds > interval_fn() * 5
        result[name] = {
            'seen': True, 'stale': stale, 'last_status': row.status,
            'last_run_at': row.last_run_at.isoformat(), 'age_seconds': int(age_seconds),
        }
    return result


def _disk_usage():
    try:
        usage = shutil.disk_usage(settings.MEDIA_ROOT if os.path.isdir(settings.MEDIA_ROOT) else '/')
        percent_used = round(usage.used / usage.total * 100, 1)
        return {
            'percent_used': percent_used, 'warning': percent_used >= settings.DISK_USAGE_WARNING_PERCENT,
            'free_bytes': usage.free,
        }
    except Exception as exc:  # noqa: BLE001
        return {'error': str(exc)[:200]}


def _backup_freshness():
    backup_dir = settings.BACKUP_DIR
    if not os.path.isdir(backup_dir):
        return {'found': False, 'stale': True, 'detail': f'{backup_dir} does not exist yet'}
    candidates = [
        os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
        if f.startswith('rastichat-db-') and f.endswith('.sql.gz')
    ]
    if not candidates:
        return {'found': False, 'stale': True, 'detail': 'no backup files found'}
    newest = max(candidates, key=os.path.getmtime)
    age_hours = (timezone.now().timestamp() - os.path.getmtime(newest)) / 3600
    return {
        'found': True, 'stale': age_hours > settings.BACKUP_MAX_AGE_HOURS,
        'newest_file': os.path.basename(newest), 'age_hours': round(age_hours, 1),
    }


class MonitoringView(APIView):
    """Operational visibility — deliberately separate from Liveness/
    Readiness (which gate traffic routing): a stale backup or a scheduler
    that hasn't run recently shouldn't make the app start failing user
    requests, but it absolutely should be visible to whatever's polling
    this for alerting. See docs/runbooks/MONITORING_RUNBOOK.md.

    Unlike Liveness/Readiness, this is never anonymous in staging/
    production: disk usage, backup filenames/ages, and scheduler heartbeat
    status are internal operational details, not something to hand to
    every unauthenticated caller on the internet — requires the
    X-Monitoring-Token header (settings.MONITORING_TOKEN). An unauthorized
    request gets a bare 401 with no operational detail at all, not even
    which check would have failed.
    """
    permission_classes = []

    def get(self, request):
        if not _is_authorized_monitor(request):
            return Response({'error': 'unauthorized'}, status=401)
        return Response({
            'schedulers': _scheduler_status(),
            'disk': _disk_usage(),
            'backup': _backup_freshness(),
        })


class HealthCheckView(APIView):
    """Kept at its original path/shape (`/api/v1/health/`) for backward
    compatibility with anything already polling it; equivalent to
    ReadinessView's dependency checks, without the migration-state detail.
    """
    permission_classes = []

    def get(self, request):
        db_ok, db_error = _check_database()
        redis_ok, redis_error = _check_redis()
        ready = db_ok and redis_ok
        health = {
            'status': 'healthy' if ready else 'unhealthy',
            'components': {
                'database': 'up' if db_ok else 'down',
                'redis': 'up' if redis_ok else 'down',
            },
        }
        return Response(health, status=200 if ready else 503)
