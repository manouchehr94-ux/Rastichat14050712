import os
import time

from django.core.management import call_command
from django.test import TestCase, override_settings

from common.models import SchedulerHeartbeat


class HealthcheckHostTests(TestCase):
    """backend/Dockerfile.prod's own HEALTHCHECK curls
    http://127.0.0.1:PORT/api/v1/health/live/ from *inside* the container —
    that request's Host header is literally '127.0.0.1:8000'. Proves
    settings.py's ALLOWED_HOSTS append (see config/settings.py, right after
    the ALLOWED_HOSTS fail-fast guard) actually lets that request through
    rather than getting rejected with DisallowedHost, which would
    permanently fail the container's own healthcheck (and therefore every
    depends_on: condition: service_healthy in docker-compose.staging.yml)
    on every real deployment — this exact failure was caught for real in
    CI once the docker-build job got far enough to actually start the
    backend container.
    """

    def test_loopback_host_header_is_allowed(self):
        res = self.client.get('/api/v1/health/live/', HTTP_HOST='127.0.0.1:8000')
        self.assertEqual(res.status_code, 200)

    def test_localhost_host_header_is_allowed(self):
        res = self.client.get('/api/v1/health/live/', HTTP_HOST='localhost:8000')
        self.assertEqual(res.status_code, 200)

    def test_arbitrary_host_header_is_still_rejected(self):
        # The fix is scoped to loopback/localhost only — proves it didn't
        # accidentally widen ALLOWED_HOSTS into a wildcard.
        with override_settings(ALLOWED_HOSTS=['127.0.0.1', 'localhost']):
            res = self.client.get('/api/v1/health/live/', HTTP_HOST='some-attacker-controlled-host.example')
        self.assertEqual(res.status_code, 400)


class SchedulerHeartbeatCommandTests(TestCase):
    def test_records_and_updates_heartbeat(self):
        call_command('record_scheduler_heartbeat', 'automation-worker', 'SUCCESS')
        row = SchedulerHeartbeat.objects.get(name='automation-worker')
        self.assertEqual(row.status, 'SUCCESS')

        call_command('record_scheduler_heartbeat', 'automation-worker', 'FAILURE', '--detail=boom')
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILURE')
        self.assertEqual(row.detail, 'boom')
        self.assertEqual(SchedulerHeartbeat.objects.count(), 1)


class MonitoringViewTests(TestCase):
    def test_reports_scheduler_never_seen_as_stale(self):
        res = self.client.get('/api/v1/health/monitoring/')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['schedulers']['automation-worker']['seen'])
        self.assertTrue(res.data['schedulers']['automation-worker']['stale'])

    def test_reports_recent_heartbeat_as_not_stale(self):
        call_command('record_scheduler_heartbeat', 'automation-worker', 'SUCCESS')
        res = self.client.get('/api/v1/health/monitoring/')
        self.assertFalse(res.data['schedulers']['automation-worker']['stale'])
        self.assertEqual(res.data['schedulers']['automation-worker']['last_status'], 'SUCCESS')

    @override_settings(AUTOMATION_WORKER_INTERVAL_SECONDS=1)
    def test_reports_old_heartbeat_as_stale(self):
        call_command('record_scheduler_heartbeat', 'automation-worker', 'SUCCESS')
        time.sleep(1.1 * 5)  # older than interval(1s) * 5x staleness margin
        res = self.client.get('/api/v1/health/monitoring/')
        self.assertTrue(res.data['schedulers']['automation-worker']['stale'])

    def test_reports_missing_backup_dir_as_stale(self):
        with override_settings(BACKUP_DIR='/nonexistent/rastichat-backups'):
            res = self.client.get('/api/v1/health/monitoring/')
        self.assertFalse(res.data['backup']['found'])
        self.assertTrue(res.data['backup']['stale'])

    def test_reports_fresh_backup_file(self):
        backup_dir = '/tmp/rastichat-test-backups'
        os.makedirs(backup_dir, exist_ok=True)
        try:
            with open(os.path.join(backup_dir, 'rastichat-db-20260101-000000.sql.gz'), 'wb') as f:
                f.write(b'fake')
            with override_settings(BACKUP_DIR=backup_dir):
                res = self.client.get('/api/v1/health/monitoring/')
            self.assertTrue(res.data['backup']['found'])
            self.assertFalse(res.data['backup']['stale'])
        finally:
            import shutil
            shutil.rmtree(backup_dir, ignore_errors=True)

    def test_disk_usage_reported(self):
        res = self.client.get('/api/v1/health/monitoring/')
        self.assertIn('percent_used', res.data['disk'])


class MonitoringViewAuthTests(TestCase):
    """MONITORING_TOKEN is unset under the default test settings (ENVIRONMENT
    defaults to 'development', which doesn't require it) — the tests above
    exercise that dev-permissive path. These exercise the actual gate by
    explicitly configuring a token, proving an unauthenticated/wrong-token
    caller is rejected with no operational detail, and a correctly
    authenticated one still gets the full payload.
    """

    def test_missing_token_is_rejected_with_no_detail(self):
        with override_settings(MONITORING_TOKEN='the-real-token'):
            res = self.client.get('/api/v1/health/monitoring/')
        self.assertEqual(res.status_code, 401)
        self.assertNotIn('schedulers', res.data)
        self.assertNotIn('disk', res.data)
        self.assertNotIn('backup', res.data)

    def test_wrong_token_is_rejected(self):
        with override_settings(MONITORING_TOKEN='the-real-token'):
            res = self.client.get('/api/v1/health/monitoring/', HTTP_X_MONITORING_TOKEN='not-it')
        self.assertEqual(res.status_code, 401)

    def test_correct_token_is_accepted(self):
        with override_settings(MONITORING_TOKEN='the-real-token'):
            res = self.client.get('/api/v1/health/monitoring/', HTTP_X_MONITORING_TOKEN='the-real-token')
        self.assertEqual(res.status_code, 200)
        self.assertIn('schedulers', res.data)
        self.assertIn('disk', res.data)
        self.assertIn('backup', res.data)


class ReadinessViewErrorDetailTests(TestCase):
    """Anonymous callers see `up: true/false`, never the raw exception text
    (hostnames, ports, driver-specific error strings) — only a caller
    presenting a valid MONITORING_TOKEN does. Forces Redis down via an
    unroutable host/port so a real, deterministic error string exists to
    check for, rather than relying on Redis happening to already be down.
    """

    def test_anonymous_caller_does_not_see_redis_error_detail(self):
        with override_settings(
            MONITORING_TOKEN='the-real-token',
            REDIS_CONNECTION={'host': '127.0.0.1', 'port': 1, 'password': None, 'db': 0},
        ):
            res = self.client.get('/api/v1/health/ready/')
        self.assertEqual(res.status_code, 503)
        self.assertFalse(res.data['components']['redis']['up'])
        self.assertNotIn('error', res.data['components']['redis'])

    def test_authorized_caller_sees_redis_error_detail(self):
        with override_settings(
            MONITORING_TOKEN='the-real-token',
            REDIS_CONNECTION={'host': '127.0.0.1', 'port': 1, 'password': None, 'db': 0},
        ):
            res = self.client.get('/api/v1/health/ready/', HTTP_X_MONITORING_TOKEN='the-real-token')
        self.assertEqual(res.status_code, 503)
        self.assertFalse(res.data['components']['redis']['up'])
        self.assertIn('error', res.data['components']['redis'])
