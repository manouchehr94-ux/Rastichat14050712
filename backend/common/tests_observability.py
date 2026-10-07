"""Master spec §36: structured events and counters never carry credentials and are visible on the monitoring endpoint."""
import logging

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from common import observability
from integrations.testing import FakeHost
from integrations.tests import flush_redis_state


class EmitTests(TestCase):
    def setUp(self):
        flush_redis_state()
        from integrations.redis_store import get_redis
        for key in get_redis().scan_iter(match=f'{observability.COUNTER_PREFIX}:*'):
            get_redis().delete(key)

    def test_counts_and_logs_one_line(self):
        with self.assertLogs('rastichat.events', level='INFO') as logs:
            observability.emit('identity_exchange', label='customer', integration='acme')
            observability.emit('identity_exchange', label='customer', integration='acme')
        self.assertEqual(len(logs.output), 2)
        self.assertIn('event=identity_exchange', logs.output[0])
        self.assertEqual(observability.counters()['identity_exchange:customer'], 2)

    def test_credential_looking_fields_are_dropped_and_values_sanitised(self):
        with self.assertLogs('rastichat.events', level='INFO') as logs:
            observability.emit('x', token='SECRET-TOKEN', assertion='eyJ.a.b', password='pw', integration='a b\nc;d')
        line = logs.output[0]
        for leaked in ('SECRET-TOKEN', 'eyJ.a.b', 'pw'):
            self.assertNotIn(leaked, line)
        self.assertEqual(line.count('\n'), 0)
        self.assertIn('integration=a_b_c_d', line)

    def test_never_raises_when_redis_is_down(self):
        from unittest import mock
        with mock.patch('integrations.redis_store.get_redis', side_effect=RuntimeError('redis down')):
            observability.emit('anything', label='x')   # must not raise


class InstrumentationTests(TestCase):
    def setUp(self):
        flush_redis_state()
        from integrations.redis_store import get_redis
        for key in get_redis().scan_iter(match=f'{observability.COUNTER_PREFIX}:*'):
            get_redis().delete(key)
        self.client = APIClient()

    def test_integration_activity_is_counted_and_reported_by_monitoring(self):
        host = FakeHost('acme')
        with self.captureOnCommitCallbacks(execute=True):
            host.call(self.client, 'put', '/api/v1/integrations/tenants/t1/', {'display_name': 'T1'})
        other = FakeHost('mallory')
        other.call(self.client, 'get', '/api/v1/integrations/tenants/t1/')                       # cross-integration: refused
        host.call(self.client, 'get', '/api/v1/integrations/me/', kid='ick_unknown')              # unknown key: refused
        counters = observability.counters()
        self.assertEqual(counters.get('audit:tenant_provisioned'), 1)
        self.assertGreaterEqual(counters.get('token_refused:invalid_token', 0) + counters.get('token_refused:unknown_key', 0), 1)
        self.assertTrue(any(k.startswith('api_error:') for k in counters))
        with override_settings(MONITORING_TOKEN='m-token'):
            res = self.client.get('/api/v1/health/monitoring/', HTTP_X_MONITORING_TOKEN='m-token')
            self.assertEqual(res.status_code, 200)
            self.assertIn('audit:tenant_provisioned', res.json()['events_last_24h'])
            self.assertEqual(self.client.get('/api/v1/health/monitoring/').status_code, 401)   # still not anonymous
