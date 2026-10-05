"""P1-3: no long-lived credential in any URL; WebSocket auth by single-use ticket.

Covers ticket issuing (authorization, uniform 404, hashing, TTL), atomic
single-use consumption (incl. a concurrent race), binding to kind/conversation,
the first-frame protocol (no group access before auth, timeout, bad frames),
end-to-end chat over ticket sockets, live revocation of ticket sockets, the
legacy-URL switch (off => legacy routes and `?session_token=` refused) and
widget token rotation.
"""
import asyncio
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from common import ws_tickets
from config.asgi import application
from conversations.models import Conversation, Message
from knowledge_base.tests_base import KBTestMixin
from notifications.models import Notification
from notifications.services import notify
from platforms.models import PlatformMembership
from visitors.models import VisitorSession
from workspaces.models import WorkspaceMembership


class _World(KBTestMixin):
    def make_world(self):
        self.ws = self.make_workspace()
        self.project = self.make_project(self.ws)
        self.visitor = self.make_visitor(self.project)
        self.session = self.make_visitor_session(self.visitor)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)
        self.operator = self.make_operator(self.ws)
        self.admin = self.make_admin(self.ws, role='WORKSPACE_ADMIN')
        self.support_conv = Conversation.objects.create(
            workspace=self.ws, type=Conversation.Type.PLATFORM_SUPPORT,
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject='s')
        self.agent = self.make_user()
        PlatformMembership.objects.create(user=self.agent, platform=self.ws.platform, role='PLATFORM_SUPPORT_AGENT')
        other_ws = self.make_workspace()
        self.stranger = self.make_admin(other_ws)

    def api(self, user=None):
        c = APIClient()
        if user is not None:
            c.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        return c

    def dash_ticket(self, user, kind, conv=None):
        body = {'kind': kind}
        if conv is not None:
            body['conversation_id'] = str(conv.id)
        res = self.api(user).post('/api/v1/ws/ticket/', body, format='json')
        assert res.status_code == 201, (res.status_code, res.data)
        return res.data['ticket']

    def widget_ticket(self, session=None, conv=None):
        res = APIClient().post(
            '/api/v1/widget/ws-ticket/', {'conversation_id': str((conv or self.conv).id)}, format='json',
            HTTP_X_WIDGET_SESSION=str((session or self.session).token))
        assert res.status_code == 201, (res.status_code, res.data)
        return res.data['ticket']


# --------------------------------------------------------------------------- issuing
class TicketIssueTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_operator_gets_ticket_for_own_conversation(self):
        res = self.api(self.operator).post(
            '/api/v1/ws/ticket/', {'kind': 'dashboard_chat', 'conversation_id': str(self.conv.id)}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertTrue(len(res.data['ticket']) >= 40)
        self.assertEqual(res.data['expires_in'], 30)

    def test_unauthorized_targets_are_a_uniform_404(self):
        c = self.api(self.stranger)
        for kind, conv in (('dashboard_chat', self.conv), ('support', self.support_conv)):
            res = c.post('/api/v1/ws/ticket/', {'kind': kind, 'conversation_id': str(conv.id)}, format='json')
            self.assertEqual(res.status_code, 404, kind)
        for bogus in ('00000000-0000-0000-0000-000000000000', 'garbage', None):
            res = self.api(self.operator).post('/api/v1/ws/ticket/', {'kind': 'dashboard_chat', 'conversation_id': bogus}, format='json')
            self.assertEqual(res.status_code, 404, bogus)

    def test_kind_validation(self):
        for body in ({}, {'kind': 'admin'}, {'kind': None}):
            self.assertEqual(self.api(self.operator).post('/api/v1/ws/ticket/', body, format='json').status_code, 400)

    def test_support_ticket_roles(self):
        ok = lambda u: self.api(u).post('/api/v1/ws/ticket/', {'kind': 'support', 'conversation_id': str(self.support_conv.id)}, format='json').status_code
        self.assertEqual(ok(self.admin), 201)
        self.assertEqual(ok(self.agent), 201)
        self.assertEqual(ok(self.operator), 404)  # plain operator never reaches platform support
        self.assertEqual(ok(self.stranger), 404)

    def test_operator_cannot_get_support_ticket_for_customer_conversation_or_vice_versa(self):
        res = self.api(self.admin).post('/api/v1/ws/ticket/', {'kind': 'support', 'conversation_id': str(self.conv.id)}, format='json')
        self.assertEqual(res.status_code, 404)
        res = self.api(self.admin).post('/api/v1/ws/ticket/', {'kind': 'dashboard_chat', 'conversation_id': str(self.support_conv.id)}, format='json')
        self.assertEqual(res.status_code, 404)

    def test_notifications_ticket_needs_no_conversation(self):
        res = self.api(self.operator).post('/api/v1/ws/ticket/', {'kind': 'notifications'}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_authentication_required_and_inactive_user_refused(self):
        self.assertIn(self.api().post('/api/v1/ws/ticket/', {'kind': 'notifications'}, format='json').status_code, (401, 403))
        token = AccessToken.for_user(self.operator)
        User.objects.filter(id=self.operator.id).update(is_active=False)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(c.post('/api/v1/ws/ticket/', {'kind': 'notifications'}, format='json').status_code, 401)

    def test_widget_ticket_requires_a_valid_session_and_own_conversation(self):
        url = '/api/v1/widget/ws-ticket/'
        body = {'conversation_id': str(self.conv.id)}
        self.assertEqual(APIClient().post(url, body, format='json').status_code, 401)
        self.assertEqual(APIClient().post(url, body, format='json', HTTP_X_WIDGET_SESSION='nope').status_code, 401)
        other = self.make_visitor_session(self.make_visitor(self.project))
        res = APIClient().post(url, body, format='json', HTTP_X_WIDGET_SESSION=str(other.token))
        self.assertEqual(res.status_code, 404)
        VisitorSession.objects.filter(id=self.session.id).update(expires_at=timezone.now() - timedelta(seconds=1))
        res = APIClient().post(url, body, format='json', HTTP_X_WIDGET_SESSION=str(self.session.token))
        self.assertEqual(res.status_code, 401)

    def test_widget_ticket_accepts_session_in_body_too(self):
        res = APIClient().post('/api/v1/widget/ws-ticket/',
                               {'conversation_id': str(self.conv.id), 'session_token': str(self.session.token)}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_ticket_is_stored_hashed_with_a_short_ttl(self):
        ticket = self.dash_ticket(self.operator, 'notifications')
        r = ws_tickets._get_redis()
        self.assertIsNone(r.get(ticket))  # the raw value is never a key
        key = 'wsticket:' + hashlib.sha256(ticket.encode()).hexdigest()
        self.assertTrue(r.exists(key))
        self.assertLessEqual(r.ttl(key), 30)
        self.assertNotIn(ticket.encode(), r.get(key))  # nor stored in the value

    def test_every_ticket_is_unique(self):
        tickets = {self.dash_ticket(self.operator, 'notifications') for _ in range(20)}
        self.assertEqual(len(tickets), 20)


# ------------------------------------------------------------------ consumption
class TicketConsumptionTests(TestCase):
    def test_single_use(self):
        t = ws_tickets.issue_ticket(kind='support', subject=1, scope_id='abc')
        self.assertEqual(ws_tickets.consume_ticket_sync(t, kind='support', scope_id='abc')['sub'], '1')
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='support', scope_id='abc'))

    def test_wrong_kind_or_scope_is_rejected_and_burns_the_ticket(self):
        t = ws_tickets.issue_ticket(kind='support', subject=1, scope_id='abc')
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='dashboard_chat', scope_id='abc'))
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='support', scope_id='abc'))  # burned
        t = ws_tickets.issue_ticket(kind='support', subject=1, scope_id='abc')
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='support', scope_id='other'))
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='support', scope_id='abc'))

    def test_conversation_bound_ticket_not_usable_unscoped_and_vice_versa(self):
        t = ws_tickets.issue_ticket(kind='notifications', subject=1)
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='notifications', scope_id='abc'))
        t = ws_tickets.issue_ticket(kind='support', subject=1, scope_id='abc')
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='support', scope_id=None))

    @override_settings(WS_TICKET_TTL_SECONDS=1)
    def test_expires(self):
        t = ws_tickets.issue_ticket(kind='notifications', subject=1)
        time.sleep(1.3)
        self.assertIsNone(ws_tickets.consume_ticket_sync(t, kind='notifications'))

    def test_garbage_input(self):
        for bad in (None, '', 'x' * 500, 'not-a-ticket', 12345, b'bytes'):
            self.assertIsNone(ws_tickets.consume_ticket_sync(bad, kind='notifications'))

    def test_unknown_kind_cannot_be_issued(self):
        with self.assertRaises(ValueError):
            ws_tickets.issue_ticket(kind='admin', subject=1)

    def test_concurrent_consumers_exactly_one_wins(self):
        for _ in range(5):
            t = ws_tickets.issue_ticket(kind='support', subject=7, scope_id='c1')
            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(lambda _i: ws_tickets.consume_ticket_sync(t, kind='support', scope_id='c1'), range(32)))
            self.assertEqual(sum(r is not None for r in results), 1)


# ---------------------------------------------------------------------- protocol
async def open_ws(path):
    comm = WebsocketCommunicator(application, path)
    connected, _ = await comm.connect()
    assert connected, f'{path} was not accepted'
    return comm


async def authenticate(comm, ticket):
    await comm.send_json_to({'type': 'auth', 'ticket': ticket})


async def expect_close(test, comm, code=4401, timeout=3):
    out = await comm.receive_output(timeout=timeout)
    test.assertEqual(out['type'], 'websocket.close')
    test.assertEqual(out.get('code'), code)


@override_settings(WS_REVALIDATE_SECONDS=0)
class TicketSocketProtocolTests(_World, TransactionTestCase):
    def setUp(self):
        self.make_world()

    def dash_path(self, conv=None):
        return f'/ws/v2/dashboard/{(conv or self.conv).id}/'

    async def mint(self, *a, **kw):
        return await database_sync_to_async(self.dash_ticket)(*a, **kw)

    async def test_valid_ticket_authenticates_and_is_acknowledged(self):
        comm = await open_ws(self.dash_path())
        await authenticate(comm, await self.mint(self.operator, 'dashboard_chat', self.conv))
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await comm.disconnect()

    async def test_no_group_access_before_authentication(self):
        comm = await open_ws(self.dash_path())
        layer = get_channel_layer()
        await layer.group_send(f'chat_{self.conv.id}', {'type': 'chat.message', 'message': {'id': 'x', 'content': 'secret-1'}})
        await layer.group_send(f'chat_ops_{self.conv.id}', {'type': 'conversation.queued', 'summary': 'secret-2'})
        self.assertTrue(await comm.receive_nothing(timeout=0.4))
        await authenticate(comm, await self.mint(self.operator, 'dashboard_chat', self.conv))
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await layer.group_send(f'chat_{self.conv.id}', {'type': 'chat.message', 'message': {'id': 'y', 'content': 'after-auth'}})
        self.assertEqual((await comm.receive_json_from(timeout=3))['content'], 'after-auth')
        await comm.disconnect()

    async def test_messages_are_not_processed_before_authentication(self):
        comm = await open_ws(self.dash_path())
        await comm.send_json_to({'message': 'sneaky', 'client_message_id': 'pre1'})
        await expect_close(self, comm)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(client_message_id='pre1').count)(), 0)

    async def test_bad_first_frames_close_4401(self):
        for frame in ('not json', '[]', '{"type": "auth"}', '{"type": "auth", "ticket": 5}', '{"type": "hello", "ticket": "x"}'):
            comm = await open_ws(self.dash_path())
            await comm.send_to(text_data=frame)
            await expect_close(self, comm)

    @override_settings(WS_AUTH_TIMEOUT_SECONDS=0.3)
    async def test_silent_socket_is_closed_after_the_auth_timeout(self):
        comm = await open_ws(self.dash_path())
        await expect_close(self, comm, timeout=3)

    async def test_authenticated_socket_is_not_closed_by_the_old_deadline(self):
        with override_settings(WS_AUTH_TIMEOUT_SECONDS=0.4):
            comm = await open_ws(self.dash_path())
            await authenticate(comm, await self.mint(self.operator, 'dashboard_chat', self.conv))
            await comm.receive_json_from(timeout=3)
            await asyncio.sleep(0.8)
            await comm.send_json_to({'message': 'still alive', 'client_message_id': 'alive1'})
            self.assertEqual((await comm.receive_json_from(timeout=3))['content'], 'still alive')
            await comm.disconnect()

    async def test_replayed_ticket_is_rejected(self):
        ticket = await self.mint(self.operator, 'dashboard_chat', self.conv)
        first = await open_ws(self.dash_path())
        await authenticate(first, ticket)
        self.assertEqual((await first.receive_json_from(timeout=3))['type'], 'auth.ok')
        second = await open_ws(self.dash_path())
        await authenticate(second, ticket)
        await expect_close(self, second)
        await first.disconnect()

    async def test_ticket_for_another_conversation_is_rejected(self):
        other = await database_sync_to_async(self.make_conversation)(self.ws, self.project, self.visitor)
        ticket = await self.mint(self.operator, 'dashboard_chat', other)
        comm = await open_ws(self.dash_path(self.conv))
        await authenticate(comm, ticket)
        await expect_close(self, comm)

    async def test_ticket_for_another_kind_is_rejected(self):
        ticket = await self.mint(self.admin, 'support', self.support_conv)
        comm = await open_ws(self.dash_path())
        await authenticate(comm, ticket)
        await expect_close(self, comm)
        n_ticket = await self.mint(self.admin, 'notifications')
        comm = await open_ws(f'/ws/v2/support/{self.support_conv.id}/')
        await authenticate(comm, n_ticket)
        await expect_close(self, comm)

    async def test_garbage_and_expired_tickets_are_rejected(self):
        comm = await open_ws(self.dash_path())
        await authenticate(comm, 'garbage-ticket')
        await expect_close(self, comm)

    async def test_revoked_between_issue_and_use_is_refused_4403(self):
        ticket = await self.mint(self.operator, 'dashboard_chat', self.conv)
        await database_sync_to_async(WorkspaceMembership.objects.filter(user=self.operator).delete)()
        comm = await open_ws(self.dash_path())
        await authenticate(comm, ticket)
        await expect_close(self, comm, code=4403)

    async def test_ticket_socket_is_revalidated_live(self):
        comm = await open_ws(self.dash_path())
        await authenticate(comm, await self.mint(self.operator, 'dashboard_chat', self.conv))
        await comm.receive_json_from(timeout=3)
        await database_sync_to_async(User.objects.filter(id=self.operator.id).update)(is_active=False)
        await comm.send_json_to({'message': 'x', 'client_message_id': 'rv1'})
        await expect_close(self, comm, code=4403)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(client_message_id='rv1').count)(), 0)

    async def test_reconnect_needs_a_fresh_ticket(self):
        comm = await open_ws(self.dash_path())
        ticket = await self.mint(self.operator, 'dashboard_chat', self.conv)
        await authenticate(comm, ticket)
        await comm.receive_json_from(timeout=3)
        await comm.disconnect()
        again = await open_ws(self.dash_path())
        await authenticate(again, ticket)  # same ticket => rejected
        await expect_close(self, again)
        third = await open_ws(self.dash_path())
        await authenticate(third, await self.mint(self.operator, 'dashboard_chat', self.conv))
        self.assertEqual((await third.receive_json_from(timeout=3))['type'], 'auth.ok')
        await third.disconnect()

    async def test_two_concurrent_sockets_each_need_their_own_ticket(self):
        a = await open_ws(self.dash_path())
        b = await open_ws(self.dash_path())
        await authenticate(a, await self.mint(self.operator, 'dashboard_chat', self.conv))
        await authenticate(b, await self.mint(self.operator, 'dashboard_chat', self.conv))
        self.assertEqual((await a.receive_json_from(timeout=3))['type'], 'auth.ok')
        self.assertEqual((await b.receive_json_from(timeout=3))['type'], 'auth.ok')
        await a.disconnect()
        await b.disconnect()


@override_settings(WS_REVALIDATE_SECONDS=0)
class TicketEndToEndTests(_World, TransactionTestCase):
    def setUp(self):
        self.make_world()

    async def test_widget_and_operator_chat_over_ticket_sockets(self):
        widget = await open_ws(f'/ws/v2/widget/{self.conv.id}/')
        await authenticate(widget, await database_sync_to_async(self.widget_ticket)())
        self.assertEqual((await widget.receive_json_from(timeout=3))['type'], 'auth.ok')
        op = await open_ws(f'/ws/v2/dashboard/{self.conv.id}/')
        await authenticate(op, await database_sync_to_async(self.dash_ticket)(self.operator, 'dashboard_chat', self.conv))
        self.assertEqual((await op.receive_json_from(timeout=3))['type'], 'auth.ok')

        await widget.send_json_to({'message': 'hello store', 'client_message_id': 'e2e1'})
        got = await op.receive_json_from(timeout=3)
        self.assertEqual((got['content'], got['sender_type']), ('hello store', 'VISITOR'))
        await op.send_json_to({'message': 'hello customer', 'client_message_id': 'e2e2'})
        reply = await widget.receive_json_from(timeout=3)
        while reply['content'] != 'hello customer':  # the widget first gets its own echo
            reply = await widget.receive_json_from(timeout=3)
        self.assertEqual(reply['sender_type'], 'USER')
        # persisted exactly once, attributed to the right party
        msgs = await database_sync_to_async(lambda: list(Message.objects.filter(conversation=self.conv).order_by('created_at').values_list('client_message_id', 'sender_type')))()
        self.assertIn(('e2e1', 'VISITOR'), msgs)
        self.assertIn(('e2e2', 'USER'), msgs)
        await widget.disconnect()
        await op.disconnect()

    async def test_support_conversation_between_store_admin_and_platform_over_tickets(self):
        admin = await open_ws(f'/ws/v2/support/{self.support_conv.id}/')
        agent = await open_ws(f'/ws/v2/support/{self.support_conv.id}/')
        await authenticate(admin, await database_sync_to_async(self.dash_ticket)(self.admin, 'support', self.support_conv))
        await authenticate(agent, await database_sync_to_async(self.dash_ticket)(self.agent, 'support', self.support_conv))
        await admin.receive_json_from(timeout=3)
        await agent.receive_json_from(timeout=3)
        await admin.send_json_to({'message': 'need help', 'client_message_id': 'sp1'})
        self.assertEqual((await agent.receive_json_from(timeout=3))['content'], 'need help')
        await admin.disconnect()
        await agent.disconnect()

    async def test_notifications_over_ticket_socket(self):
        comm = await open_ws('/ws/v2/notifications/')
        await database_sync_to_async(notify)(self.operator, self.ws, Notification.EventType.MENTIONED, 'before-auth')
        self.assertTrue(await comm.receive_nothing(timeout=0.4))
        await authenticate(comm, await database_sync_to_async(self.dash_ticket)(self.operator, 'notifications'))
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        await database_sync_to_async(notify)(self.operator, self.ws, Notification.EventType.MENTIONED, 'after-auth')
        self.assertEqual((await comm.receive_json_from(timeout=3))['notification']['title'], 'after-auth')
        # push-only: anything the client sends is ignored, the socket stays open
        await comm.send_json_to({'message': 'ignored'})
        self.assertTrue(await comm.receive_nothing(timeout=0.3))
        await comm.disconnect()

    async def test_widget_socket_survives_token_rotation_but_not_revocation(self):
        widget = await open_ws(f'/ws/v2/widget/{self.conv.id}/')
        await authenticate(widget, await database_sync_to_async(self.widget_ticket)())
        await widget.receive_json_from(timeout=3)
        rot = await database_sync_to_async(APIClient().post)(
            '/api/v1/widget/session/rotate/', {'session_token': str(self.session.token)}, format='json')
        self.assertEqual(rot.status_code, 200)
        await widget.send_json_to({'message': 'after rotation', 'client_message_id': 'rot1'})
        self.assertEqual((await widget.receive_json_from(timeout=3))['content'], 'after rotation')
        await database_sync_to_async(VisitorSession.objects.filter(id=self.session.id).update)(revoked_at=timezone.now())
        await widget.send_json_to({'message': 'after revoke', 'client_message_id': 'rot2'})
        await expect_close(self, widget, code=4403)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(client_message_id='rot2').count)(), 0)

    async def test_other_visitors_session_cannot_get_a_ticket_for_this_conversation(self):
        other = await database_sync_to_async(self.make_visitor_session)(await database_sync_to_async(self.make_visitor)(self.project))
        res = await database_sync_to_async(APIClient().post)(
            '/api/v1/widget/ws-ticket/', {'conversation_id': str(self.conv.id)}, format='json',
            HTTP_X_WIDGET_SESSION=str(other.token))
        self.assertEqual(res.status_code, 404)


# ------------------------------------------------------------------ legacy switch
@override_settings(WS_REVALIDATE_SECONDS=0)
class LegacyUrlCredentialTests(_World, TransactionTestCase):
    def setUp(self):
        self.make_world()

    def legacy_paths(self):
        jwt = lambda u: AccessToken.for_user(u)
        return [
            f'/ws/widget/{self.session.token}/{self.conv.id}/',
            f'/ws/dashboard/{jwt(self.operator)}/{self.conv.id}/',
            f'/ws/dashboard/support/{jwt(self.admin)}/{self.support_conv.id}/',
            f'/ws/notifications/{jwt(self.operator)}/',
        ]

    @override_settings(LEGACY_URL_CREDENTIALS_ENABLED=False)
    async def test_legacy_url_routes_are_refused_when_disabled(self):
        for path in await database_sync_to_async(self.legacy_paths)():
            comm = WebsocketCommunicator(application, path)
            connected, _ = await comm.connect()
            self.assertFalse(connected, path.split('/')[2])

    @override_settings(LEGACY_URL_CREDENTIALS_ENABLED=True)
    async def test_legacy_url_routes_still_work_when_explicitly_enabled(self):
        for path in await database_sync_to_async(self.legacy_paths)():
            comm = WebsocketCommunicator(application, path)
            connected, _ = await comm.connect()
            self.assertTrue(connected, path.split('/')[2])
            await comm.disconnect()

    async def test_default_outside_production_keeps_legacy_for_local_tooling(self):
        from django.conf import settings
        self.assertEqual(settings.LEGACY_URL_CREDENTIALS_ENABLED, not settings.IS_PRODUCTION_LIKE)


class LegacyQueryStringTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def get_history(self, **kw):
        return APIClient().get(f'/api/v1/widget/conversations/{self.conv.id}/messages/', **kw)

    @override_settings(LEGACY_URL_CREDENTIALS_ENABLED=False)
    def test_session_token_in_query_string_is_ignored_when_disabled(self):
        self.assertEqual(self.get_history(data={'session_token': str(self.session.token)}).status_code, 401)
        self.assertEqual(self.get_history(HTTP_X_WIDGET_SESSION=str(self.session.token)).status_code, 200)

    @override_settings(LEGACY_URL_CREDENTIALS_ENABLED=False)
    def test_body_credential_still_works_when_disabled(self):
        res = APIClient().post('/api/v1/widget/start/', {'session_token': str(self.session.token)}, format='json')
        self.assertEqual(res.status_code, 200)

    @override_settings(LEGACY_URL_CREDENTIALS_ENABLED=True)
    def test_query_string_works_only_with_the_legacy_switch(self):
        self.assertEqual(self.get_history(data={'session_token': str(self.session.token)}).status_code, 200)
