"""The rollout plan for the credentials-in-URL removal, executed as tests (docs/runbooks/
WS_TICKETS_AND_URL_CREDENTIALS.md): on a staging/production-like process

  phase 1  the owner-approved window is open: ONLY already-published widget bundles may still use
           visitor session tokens in URLs; staff JWTs in URLs are refused from the first minute; the new
           ticket/header mechanism works side by side; every legacy use is counted per project;
  phase 2  the window has passed: legacy widget credentials are refused too, nothing else changes, and
           no conversation or message is lost (history is read with the header credential).
"""
import datetime
from io import StringIO

from channels.db import database_sync_to_async
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from common import legacy_credentials
from conversations.models import Message
from conversations.tests_ws_tickets import _World, authenticate, open_ws
from channels.testing import WebsocketCommunicator
from config.asgi import application


def _until(days):
    return datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=days)


def production_with_window(days):
    return override_settings(
        IS_PRODUCTION_LIKE=True, LEGACY_URL_CREDENTIALS_ENABLED=True, LEGACY_URL_CREDENTIALS_UNTIL=_until(days),
        WS_REVALIDATE_SECONDS=0,
    )


def _clear_usage():
    legacy_credentials._get_redis().delete(legacy_credentials.usage_key(legacy_credentials.utc_today()))


class PolicyTests(TestCase):
    def test_window_is_inclusive_and_staff_surface_is_never_allowed_in_production(self):
        with production_with_window(0):
            self.assertTrue(legacy_credentials.allowed('widget'))
            self.assertFalse(legacy_credentials.allowed('dashboard'))
        with production_with_window(-1):
            self.assertFalse(legacy_credentials.allowed('widget'))
            self.assertFalse(legacy_credentials.allowed('dashboard'))

    def test_a_process_whose_window_lapses_stops_accepting_without_a_restart(self):
        with production_with_window(0):
            self.assertTrue(legacy_credentials.allowed('widget'))
            tomorrow = legacy_credentials.utc_today() + datetime.timedelta(days=1)
            original = legacy_credentials.utc_today
            legacy_credentials.utc_today = lambda: tomorrow
            try:
                self.assertFalse(legacy_credentials.allowed('widget'))
            finally:
                legacy_credentials.utc_today = original

    def test_no_window_means_off_in_production_and_everything_allowed_locally(self):
        with override_settings(IS_PRODUCTION_LIKE=True, LEGACY_URL_CREDENTIALS_ENABLED=True, LEGACY_URL_CREDENTIALS_UNTIL=None):
            self.assertFalse(legacy_credentials.allowed('widget'))
        with override_settings(IS_PRODUCTION_LIKE=False, LEGACY_URL_CREDENTIALS_ENABLED=True):
            self.assertTrue(legacy_credentials.allowed('widget') and legacy_credentials.allowed('dashboard'))
        with override_settings(IS_PRODUCTION_LIKE=False, LEGACY_URL_CREDENTIALS_ENABLED=False):
            self.assertFalse(legacy_credentials.allowed('widget'))


class PhaseOneTests(_World, TransactionTestCase):
    """Window open (the old widget bundles are still out there)."""

    def setUp(self):
        self.make_world()
        _clear_usage()

    def tearDown(self):
        _clear_usage()

    def legacy_widget_path(self):
        return f'/ws/widget/{self.session.token}/{self.conv.id}/'

    @production_with_window(7)
    async def test_old_widget_bundle_keeps_working_and_is_counted_per_project(self):
        comm = WebsocketCommunicator(application, await database_sync_to_async(self.legacy_widget_path)())
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        await comm.disconnect()
        usage = await database_sync_to_async(legacy_credentials.read_usage)(1)
        self.assertEqual(usage[legacy_credentials.utc_today()][('widget', str(self.project.id))], 1)

    @production_with_window(7)
    async def test_staff_jwts_in_urls_are_refused_from_the_first_minute(self):
        def paths():
            jwt = lambda u: AccessToken.for_user(u)
            return [
                f'/ws/dashboard/{jwt(self.operator)}/{self.conv.id}/',
                f'/ws/dashboard/support/{jwt(self.admin)}/{self.support_conv.id}/',
                f'/ws/notifications/{jwt(self.operator)}/',
            ]
        for path in await database_sync_to_async(paths)():
            connected, _ = await WebsocketCommunicator(application, path).connect()
            self.assertFalse(connected, path.split('/')[2])

    @production_with_window(7)
    async def test_new_ticket_clients_work_side_by_side(self):
        ticket = await database_sync_to_async(self.widget_ticket)()
        comm = await open_ws(f'/ws/v2/widget/{self.conv.id}/')
        await authenticate(comm, ticket)
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await comm.disconnect()
        ops = await database_sync_to_async(self.dash_ticket)(self.operator, 'dashboard_chat', self.conv)
        comm = await open_ws(f'/ws/v2/dashboard/{self.conv.id}/')
        await authenticate(comm, ops)
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await comm.disconnect()

    @production_with_window(7)
    def test_legacy_rest_query_token_works_in_the_window_and_is_counted(self):
        res = APIClient().get(f'/api/v1/widget/conversations/{self.conv.id}/messages/',
                              data={'session_token': str(self.session.token)})
        self.assertEqual(res.status_code, 200)
        usage = legacy_credentials.read_usage(1)[legacy_credentials.utc_today()]
        self.assertEqual(usage[('widget_rest', '-')], 1)

    @production_with_window(7)
    def test_usage_report_lists_the_projects_still_on_an_old_bundle(self):
        legacy_credentials.record_use('widget', self.project.id)
        legacy_credentials.record_use('widget', self.project.id)
        out = StringIO()
        call_command('report_legacy_credential_usage', '--days', '3', stdout=out)
        text = out.getvalue()
        self.assertIn(str(self.project.id), text)
        self.assertIn('exception window ends', text)
        self.assertIn(': 2', text)

    def test_header_and_ticket_credentials_are_not_counted_as_legacy(self):
        with production_with_window(7):
            res = APIClient().get(f'/api/v1/widget/conversations/{self.conv.id}/messages/',
                                  HTTP_X_WIDGET_SESSION=str(self.session.token))
            self.assertEqual(res.status_code, 200)
            self.assertEqual(legacy_credentials.read_usage(1), {})


class PhaseTwoTests(_World, TransactionTestCase):
    """Window over: legacy is off for everyone; nothing is lost."""

    def setUp(self):
        self.make_world()
        self.message = Message.objects.create(
            conversation=self.conv, sender_type=Message.SenderType.VISITOR, content='پیام قدیمی')
        _clear_usage()

    @production_with_window(-1)
    async def test_old_widget_bundle_is_refused_after_the_window(self):
        path = await database_sync_to_async(lambda: f'/ws/widget/{self.session.token}/{self.conv.id}/')()
        connected, _ = await WebsocketCommunicator(application, path).connect()
        self.assertFalse(connected)

    @production_with_window(-1)
    def test_query_string_credential_is_ignored_after_the_window(self):
        res = APIClient().get(f'/api/v1/widget/conversations/{self.conv.id}/messages/',
                              data={'session_token': str(self.session.token)})
        self.assertEqual(res.status_code, 401)

    @production_with_window(-1)
    def test_new_clients_keep_full_access_to_the_unchanged_history(self):
        res = APIClient().get(f'/api/v1/widget/conversations/{self.conv.id}/messages/',
                              HTTP_X_WIDGET_SESSION=str(self.session.token))
        self.assertEqual(res.status_code, 200)
        self.assertIn('پیام قدیمی', str(res.data))
        # the body credential used by old POSTs is still accepted: sending never breaks silently
        res = APIClient().post('/api/v1/widget/start/', {'session_token': str(self.session.token)}, format='json')
        self.assertEqual(res.status_code, 200)

    @production_with_window(-1)
    async def test_ticket_clients_are_unaffected(self):
        ticket = await database_sync_to_async(self.widget_ticket)()
        comm = await open_ws(f'/ws/v2/widget/{self.conv.id}/')
        await authenticate(comm, ticket)
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await comm.disconnect()
