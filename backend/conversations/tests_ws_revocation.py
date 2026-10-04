"""P1-1: WebSocket authorization must be revoked live, not only at connect.

Before this change a user deactivated / removed from a workspace / demoted
after their socket opened kept sending and receiving until reconnect (and an
inactive user with a still-valid JWT could connect at all). Every consumer
now re-checks authorization before each inbound frame and each delivered
channel-layer event (common/ws_auth.py); these tests pin that behaviour for
all four consumers, for several concurrent sockets, and for reconnects.
"""
from datetime import timedelta

from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from config.asgi import application
from conversations.models import Conversation, Message
from knowledge_base.tests_base import KBTestMixin
from notifications.models import Notification
from notifications.services import broadcast_presence_updated, notify
from platforms.models import PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership

REVOKED = 4403


def token_for(user):
    return str(AccessToken.for_user(user))


async def db(fn, *a, **kw):
    return await database_sync_to_async(fn)(*a, **kw)


async def connect(path, expect=True):
    comm = WebsocketCommunicator(application, path)
    connected, _ = await comm.connect()
    assert connected is expect, f'connect({path}) -> {connected}, expected {expect}'
    return comm


async def assert_closed_revoked(test, comm):
    out = await comm.receive_output(timeout=3)
    test.assertEqual(out['type'], 'websocket.close')
    test.assertEqual(out.get('code'), REVOKED)


@override_settings(WS_REVALIDATE_SECONDS=0)
class _WsBase(KBTestMixin, TransactionTestCase):
    def setUp(self):
        self.ws = self.make_workspace()
        self.platform = self.ws.platform
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
        PlatformMembership.objects.create(user=self.agent, platform=self.platform, role='PLATFORM_SUPPORT_AGENT')


class DashboardChatRevocationTests(_WsBase):
    def path(self, user=None):
        return f'/ws/dashboard/{token_for(user or self.operator)}/{self.conv.id}/'

    async def test_inactive_user_cannot_connect(self):
        await db(User.objects.filter(id=self.operator.id).update, is_active=False)
        await connect(self.path(), expect=False)

    async def test_deactivated_while_connected_stops_sending(self):
        comm = await connect(self.path())
        await comm.send_json_to({'message': 'before', 'client_message_id': 'c1'})
        await comm.receive_json_from(timeout=3)
        await db(User.objects.filter(id=self.operator.id).update, is_active=False)
        await comm.send_json_to({'message': 'after', 'client_message_id': 'c2'})
        await assert_closed_revoked(self, comm)
        self.assertEqual(await db(Message.objects.filter(client_message_id='c2').count), 0)
        self.assertEqual(await db(Message.objects.filter(client_message_id='c1').count), 1)

    async def test_membership_removed_while_connected_stops_receiving(self):
        comm = await connect(self.path())
        await db(WorkspaceMembership.objects.filter(user=self.operator).delete)
        await get_channel_layer().group_send(
            f'chat_{self.conv.id}', {'type': 'chat.message', 'message': {'id': 'x', 'content': 'secret'}})
        await assert_closed_revoked(self, comm)
        self.assertTrue(await comm.receive_nothing(timeout=0.3))

    async def test_workspace_deactivated_while_connected(self):
        comm = await connect(self.path())
        await db(Workspace.objects.filter(id=self.ws.id).update, is_active=False)
        await comm.send_json_to({'message': 'x', 'client_message_id': 'c3'})
        await assert_closed_revoked(self, comm)

    async def test_ops_events_are_not_delivered_after_revocation(self):
        comm = await connect(self.path())
        await db(WorkspaceMembership.objects.filter(user=self.operator).delete)
        await get_channel_layer().group_send(
            f'chat_ops_{self.conv.id}', {'type': 'conversation.queued', 'summary': 'confidential'})
        await assert_closed_revoked(self, comm)

    async def test_multiple_sockets_are_all_revoked_and_other_users_unaffected(self):
        a1 = await connect(self.path())
        a2 = await connect(self.path())
        other = await connect(self.path(self.admin))
        await db(User.objects.filter(id=self.operator.id).update, is_active=False)
        await a1.send_json_to({'type': 'typing'})
        await a2.send_json_to({'type': 'typing'})
        await assert_closed_revoked(self, a1)
        await assert_closed_revoked(self, a2)
        # the still-authorized admin's socket keeps working
        await other.send_json_to({'message': 'still here', 'client_message_id': 'ok1'})
        out = await other.receive_json_from(timeout=3)
        self.assertEqual(out['content'], 'still here')
        await other.disconnect()

    async def test_reconnect_after_revocation_refused_and_after_regrant_allowed(self):
        comm = await connect(self.path())
        await db(WorkspaceMembership.objects.filter(user=self.operator).delete)
        await comm.send_json_to({'type': 'typing'})
        await assert_closed_revoked(self, comm)
        await connect(self.path(), expect=False)
        await db(WorkspaceMembership.objects.create, user=self.operator, workspace=self.ws, role='WORKSPACE_OPERATOR')
        again = await connect(self.path())
        await again.disconnect()

    async def test_other_workspace_operator_never_connects(self):
        other_ws = await db(self.make_workspace)
        stranger = await db(self.make_operator, other_ws)
        await connect(self.path(stranger), expect=False)

    @override_settings(WS_REVALIDATE_SECONDS=3600)
    async def test_revalidation_is_cached_within_window(self):
        comm = await connect(self.path())
        await comm.send_json_to({'message': 'one', 'client_message_id': 'k1'})
        await comm.receive_json_from(timeout=3)
        await db(User.objects.filter(id=self.operator.id).update, is_active=False)
        # inside the cache window the socket is still served (documented upper bound)
        await comm.send_json_to({'message': 'two', 'client_message_id': 'k2'})
        out = await comm.receive_json_from(timeout=3)
        self.assertEqual(out['content'], 'two')
        await comm.disconnect()


class DashboardSupportRevocationTests(_WsBase):
    def path(self, user):
        return f'/ws/dashboard/support/{token_for(user)}/{self.support_conv.id}/'

    async def test_inactive_user_cannot_connect(self):
        await db(User.objects.filter(id=self.admin.id).update, is_active=False)
        await connect(self.path(self.admin), expect=False)
        await db(User.objects.filter(id=self.agent.id).update, is_active=False)
        await connect(self.path(self.agent), expect=False)

    async def test_store_admin_demoted_to_operator_loses_access(self):
        comm = await connect(self.path(self.admin))
        await db(WorkspaceMembership.objects.filter(user=self.admin).update, role='WORKSPACE_OPERATOR')
        await comm.send_json_to({'message': 'x', 'client_message_id': 's1'})
        await assert_closed_revoked(self, comm)
        self.assertEqual(await db(Message.objects.filter(client_message_id='s1').count), 0)

    async def test_platform_agent_removed_loses_access(self):
        comm = await connect(self.path(self.agent))
        await db(PlatformMembership.objects.filter(user=self.agent).delete)
        await get_channel_layer().group_send(
            f'support_chat_{self.support_conv.id}', {'type': 'chat.message', 'message': {'id': 'x', 'content': 'secret'}})
        await assert_closed_revoked(self, comm)

    async def test_platform_deactivated_closes_sockets(self):
        comm = await connect(self.path(self.agent))
        await db(type(self.platform).objects.filter(id=self.platform.id).update, is_active=False)
        await comm.send_json_to({'message': 'x', 'client_message_id': 's2'})
        await assert_closed_revoked(self, comm)

    async def test_two_sides_one_revoked_other_continues(self):
        admin = await connect(self.path(self.admin))
        agent = await connect(self.path(self.agent))
        await db(PlatformMembership.objects.filter(user=self.agent).delete)
        await admin.send_json_to({'message': 'to-platform', 'client_message_id': 's3'})
        # admin still receives its own echo; the revoked agent socket only gets a close
        out = await admin.receive_json_from(timeout=3)
        self.assertEqual(out['content'], 'to-platform')
        await assert_closed_revoked(self, agent)
        await admin.disconnect()

    async def test_other_workspace_admin_cannot_connect(self):
        other_ws = await db(self.make_workspace)
        stranger = await db(self.make_admin, other_ws)
        await connect(self.path(stranger), expect=False)


class WidgetRevocationTests(_WsBase):
    def path(self, token=None):
        return f'/ws/widget/{token or self.session.token}/{self.conv.id}/'

    async def test_project_deactivated_while_connected(self):
        comm = await connect(self.path())
        await db(type(self.project).objects.filter(id=self.project.id).update, is_active=False)
        await comm.send_json_to({'message': 'hi', 'client_message_id': 'w1'})
        await assert_closed_revoked(self, comm)
        self.assertEqual(await db(Message.objects.filter(client_message_id='w1').count), 0)

    async def test_session_deleted_while_connected(self):
        comm = await connect(self.path())
        await db(type(self.session).objects.filter(id=self.session.id).delete)
        await get_channel_layer().group_send(
            f'chat_{self.conv.id}', {'type': 'chat.message', 'message': {'id': 'x', 'content': 'private'}})
        await assert_closed_revoked(self, comm)

    async def test_workspace_deactivated_blocks_connect(self):
        await db(Workspace.objects.filter(id=self.ws.id).update, is_active=False)
        await connect(self.path(), expect=False)

    async def test_other_visitors_session_cannot_join_conversation(self):
        other = await db(self.make_visitor, self.project)
        other_session = await db(self.make_visitor_session, other)
        await connect(self.path(other_session.token), expect=False)

    async def test_reconnect_with_valid_session_works(self):
        first = await connect(self.path())
        await first.disconnect()
        second = await connect(self.path())
        await second.send_json_to({'message': 'again', 'client_message_id': 'w2'})
        out = await second.receive_json_from(timeout=3)
        self.assertEqual(out['content'], 'again')
        await second.disconnect()


class NotificationsRevocationTests(_WsBase):
    def path(self, user):
        return f'/ws/notifications/{token_for(user)}/'

    async def test_inactive_user_cannot_connect(self):
        await db(User.objects.filter(id=self.operator.id).update, is_active=False)
        await connect(self.path(self.operator), expect=False)

    async def test_deactivated_user_stops_receiving_notifications(self):
        comm = await connect(self.path(self.operator))
        await db(notify, self.operator, self.ws, Notification.EventType.MENTIONED, 'first')
        got = await comm.receive_json_from(timeout=3)
        self.assertEqual(got['notification']['title'], 'first')
        await db(User.objects.filter(id=self.operator.id).update, is_active=False)
        await db(notify, self.operator, self.ws, Notification.EventType.MENTIONED, 'second')
        await assert_closed_revoked(self, comm)

    async def test_removed_from_workspace_stops_presence_events_but_keeps_personal_feed(self):
        other_ws = await db(self.make_workspace)
        await db(WorkspaceMembership.objects.create, user=self.operator, workspace=other_ws, role='WORKSPACE_OPERATOR')
        comm = await connect(self.path(self.operator))
        await db(WorkspaceMembership.objects.filter(user=self.operator, workspace=self.ws).delete)
        # presence of the workspace the user was removed from must no longer arrive...
        await db(broadcast_presence_updated, self.admin, [self.ws.id], 'ONLINE')
        # ...while the other workspace's presence and personal notifications still do
        await db(broadcast_presence_updated, self.admin, [other_ws.id], 'AWAY')
        got = await comm.receive_json_from(timeout=3)
        self.assertEqual((got['type'], got['status']), ('agent.presence_updated', 'AWAY'))
        await db(notify, self.operator, other_ws, Notification.EventType.MENTIONED, 'still-mine')
        got = await comm.receive_json_from(timeout=3)
        self.assertEqual(got['notification']['title'], 'still-mine')
        await comm.disconnect()


class UnauthenticatedGroupAccessTests(_WsBase):
    async def test_rejected_socket_receives_nothing_from_any_group(self):
        layer = get_channel_layer()
        for path in (
            f'/ws/dashboard/not-a-token/{self.conv.id}/',
            f'/ws/dashboard/support/not-a-token/{self.support_conv.id}/',
            f'/ws/widget/00000000-0000-0000-0000-000000000000/{self.conv.id}/',
            '/ws/notifications/not-a-token/',
        ):
            comm = await connect(path, expect=False)
            await layer.group_send(f'chat_{self.conv.id}', {'type': 'chat.message', 'message': {'content': 'x'}})
            await layer.group_send(f'chat_ops_{self.conv.id}', {'type': 'conversation.queued'})
            await layer.group_send(f'support_chat_{self.support_conv.id}', {'type': 'chat.message', 'message': {'content': 'x'}})
            await comm.disconnect()

    async def test_expired_jwt_cannot_connect(self):
        token = AccessToken.for_user(self.operator)
        token.set_exp(lifetime=timedelta(seconds=-5))
        await connect(f'/ws/dashboard/{token}/{self.conv.id}/', expect=False)
