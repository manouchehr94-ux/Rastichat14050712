"""P1-5: abuse protection for the support APIs and the public widget init.

Like the other throttle tests, this re-enables a tight rate for the scope under test (throttling is globally off under
`manage.py test`) to prove the wiring is load-bearing. Reads stay unthrottled; the limits are per authenticated user.
"""
from unittest.mock import patch

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import AccessToken

from common.throttles import StaffWriteThrottle
from common.ws_throttling import _get_redis
from config.asgi import application
from conversations.models import Conversation, Message
from knowledge_base.tests_base import KBTestMixin
from platforms.models import PlatformMembership


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class _World(KBTestMixin):
    def make_world(self):
        self.ws = self.make_workspace()
        self.admin = self.make_admin(self.ws, role='WORKSPACE_ADMIN')
        self.admin2 = self.make_admin(self.ws, role='WORKSPACE_ADMIN')
        self.agent = self.make_user()
        PlatformMembership.objects.create(user=self.agent, platform=self.ws.platform, role='PLATFORM_SUPPORT_AGENT')
        self.support_conv = Conversation.objects.create(
            workspace=self.ws, type=Conversation.Type.PLATFORM_SUPPORT,
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject='s')
        cache.clear()
        r = _get_redis()
        for key in r.scan_iter('wsrate:*'):
            r.delete(key)


class SupportRestThrottleTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def tearDown(self):
        cache.clear()

    def rates(self, rate='3/min'):
        return patch.object(StaffWriteThrottle, 'THROTTLE_RATES', {'support_write': rate})

    def test_store_admin_writes_are_throttled_after_the_limit(self):
        c = _client(self.admin)
        with self.rates():
            for i in range(3):
                res = c.post(f'/api/v1/support/{self.support_conv.id}/send_message/', {'content': 'hi', 'client_message_id': f'm{i}'}, format='json')
                self.assertEqual(res.status_code, 201, i)
            res = c.post(f'/api/v1/support/{self.support_conv.id}/send_message/', {'content': 'hi', 'client_message_id': 'm9'}, format='json')
            self.assertEqual(res.status_code, 429)
            self.assertIn('Retry-After', res)
        self.assertEqual(Message.objects.filter(conversation=self.support_conv).count(), 3)

    def test_creating_tickets_is_throttled_too(self):
        c = _client(self.admin)
        with self.rates('2/min'):
            self.assertEqual(c.post('/api/v1/support/', {'subject': 'a'}, format='json').status_code, 201)
            self.assertEqual(c.post('/api/v1/support/', {'subject': 'b'}, format='json').status_code, 201)
            self.assertEqual(c.post('/api/v1/support/', {'subject': 'c'}, format='json').status_code, 429)
        self.assertEqual(Conversation.objects.filter(workspace=self.ws, type='PLATFORM_SUPPORT').count(), 3)  # 1 fixture + 2

    def test_platform_replies_and_assignments_are_throttled(self):
        c = _client(self.agent)
        with self.rates('3/min'):
            codes = [c.post(f'/api/v1/platform/support/{self.support_conv.id}/reply/', {'content': 'x', 'client_message_id': f'r{i}'}, format='json').status_code
                     for i in range(5)]
        self.assertEqual(codes, [201, 201, 201, 429, 429])

    def test_operator_rest_send_is_throttled(self):
        project = self.make_project(self.ws)
        conv = self.make_conversation(self.ws, project, self.make_visitor(project))
        op = self.make_operator(self.ws)
        with self.rates('2/min'):
            codes = [_client(op).post(f'/api/v1/conversations/{conv.id}/send/', {'content': 'x', 'client_message_id': f'o{i}'}, format='json').status_code
                     for i in range(4)]
        self.assertEqual(codes, [201, 201, 429, 429])

    def test_reads_are_never_throttled(self):
        with self.rates('1/min'):
            c = _client(self.admin)
            for _ in range(10):
                self.assertEqual(c.get('/api/v1/support/').status_code, 200)
                self.assertEqual(c.get(f'/api/v1/support/{self.support_conv.id}/messages/').status_code, 200)
            for _ in range(10):
                self.assertEqual(_client(self.agent).get('/api/v1/platform/support/').status_code, 200)

    def test_limit_is_per_user(self):
        with self.rates('1/min'):
            first = _client(self.admin).post(f'/api/v1/support/{self.support_conv.id}/send_message/', {'content': 'a', 'client_message_id': 'u1'}, format='json')
            again = _client(self.admin).post(f'/api/v1/support/{self.support_conv.id}/send_message/', {'content': 'a', 'client_message_id': 'u2'}, format='json')
            other = _client(self.admin2).post(f'/api/v1/support/{self.support_conv.id}/send_message/', {'content': 'b', 'client_message_id': 'u3'}, format='json')
        self.assertEqual((first.status_code, again.status_code, other.status_code), (201, 429, 201))

    def test_unthrottled_by_default_in_tests_and_rate_is_configured_for_production(self):
        from django.conf import settings
        self.assertIsNone(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['support_write'])  # TESTING
        self.assertEqual(settings.STAFF_WS_MESSAGE_RATE_LIMIT, 60)


class WidgetInitThrottleTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def tearDown(self):
        cache.clear()

    def test_widget_init_is_throttled_and_creates_nothing_once_limited(self):
        from visitors.models import VisitorSession
        project = self.make_project(self.ws)
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'widget_init': '2/min'}):
            codes = [APIClient().post('/api/v1/widget/init/', {'project_key': str(project.public_key)}, format='json').status_code
                     for _ in range(4)]
        self.assertEqual(codes, [200, 200, 429, 429])
        self.assertEqual(VisitorSession.objects.count(), 2)


@override_settings(STAFF_WS_MESSAGE_RATE_LIMIT=3, STAFF_WS_MESSAGE_RATE_WINDOW_SECONDS=60, WS_REVALIDATE_SECONDS=0)
class StaffWebSocketRateLimitTests(_World, TransactionTestCase):
    def setUp(self):
        self.make_world()
        self.project = self.make_project(self.ws)
        self.visitor = self.make_visitor(self.project)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)
        self.operator = self.make_operator(self.ws)

    async def open(self, path):
        comm = WebsocketCommunicator(application, path)
        ok, _ = await comm.connect()
        self.assertTrue(ok)
        return comm

    def support_path(self, user):
        return f'/ws/dashboard/support/{AccessToken.for_user(user)}/{self.support_conv.id}/'

    async def test_support_socket_limits_sends_per_user_and_says_so(self):
        comm = await self.open(await database_sync_to_async(self.support_path)(self.admin))
        for i in range(3):
            await comm.send_json_to({'message': f'm{i}', 'client_message_id': f'ws{i}'})
            self.assertEqual((await comm.receive_json_from(timeout=3))['content'], f'm{i}')
        await comm.send_json_to({'message': 'over', 'client_message_id': 'ws-over'})
        out = await comm.receive_json_from(timeout=3)
        self.assertEqual(out['type'], 'rate_limited')
        self.assertEqual(out['retry_after'], 60)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(client_message_id='ws-over').count)(), 0)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(conversation=self.support_conv).count)(), 3)
        await comm.disconnect()

    async def test_other_users_are_not_affected_and_limits_are_per_user_not_per_socket(self):
        a1 = await self.open(await database_sync_to_async(self.support_path)(self.admin))
        a2 = await self.open(await database_sync_to_async(self.support_path)(self.admin))
        for i in range(2):
            await a1.send_json_to({'message': f'x{i}', 'client_message_id': f'a1-{i}'})
        await a2.send_json_to({'message': 'y0', 'client_message_id': 'a2-0'})
        await a2.send_json_to({'message': 'y1', 'client_message_id': 'a2-1'})  # 4th send by the same user across sockets
        await asyncio_sleep()
        self.assertEqual(await database_sync_to_async(Message.objects.filter(conversation=self.support_conv).count)(), 3)
        agent = await self.open(await database_sync_to_async(self.support_path)(self.agent))
        await agent.send_json_to({'message': 'agent ok', 'client_message_id': 'ag-0'})
        await asyncio_sleep()
        self.assertTrue(await database_sync_to_async(Message.objects.filter(client_message_id='ag-0').exists)())
        for c in (a1, a2, agent):
            await c.disconnect()

    async def test_typing_and_mark_read_do_not_consume_the_budget(self):
        token = await database_sync_to_async(AccessToken.for_user)(self.operator)
        comm = await self.open(f'/ws/dashboard/{token}/{self.conv.id}/')
        for _ in range(10):
            await comm.send_json_to({'type': 'typing'})
            await comm.send_json_to({'type': 'mark_read'})
        for i in range(3):
            await comm.send_json_to({'message': f'o{i}', 'client_message_id': f'op{i}'})
        await asyncio_sleep()
        self.assertEqual(await database_sync_to_async(Message.objects.filter(client_message_id__startswith='op').count)(), 3)
        await comm.send_json_to({'message': 'o-over', 'client_message_id': 'op-over'})
        await asyncio_sleep()
        self.assertEqual(await database_sync_to_async(Message.objects.filter(client_message_id='op-over').count)(), 0)
        await comm.disconnect()


async def asyncio_sleep(seconds=0.6):
    import asyncio
    await asyncio.sleep(seconds)
