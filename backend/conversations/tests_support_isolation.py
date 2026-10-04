"""Regression tests for store-admin <-> platform support isolation (P0-1).

Covers the audited holes: a user who is admin of workspace A but only an
operator of workspace B must not list/read/write/edit/delete B's support
conversations, and a multi-workspace admin must choose the target workspace
explicitly (never `.first()`).
"""
import uuid

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import User
from config.asgi import application
from conversations.models import Conversation, Message
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class _Base:
    def make_world(self):
        self.platform = Platform.objects.create(name='P')
        self.ws_a = Workspace.objects.create(name='A', platform=self.platform)
        self.ws_b = Workspace.objects.create(name='B', platform=self.platform)
        self.ws_c = Workspace.objects.create(name='C', platform=self.platform)
        # admin of A, plain operator of B
        self.mixed = User.objects.create_user(email='mixed@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.mixed, workspace=self.ws_a, role='WORKSPACE_ADMIN')
        WorkspaceMembership.objects.create(user=self.mixed, workspace=self.ws_b, role='WORKSPACE_OPERATOR')
        # admin of both A and B
        self.multi = User.objects.create_user(email='multi@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.multi, workspace=self.ws_a, role='WORKSPACE_ADMIN')
        WorkspaceMembership.objects.create(user=self.multi, workspace=self.ws_b, role='WORKSPACE_OWNER')
        # admin of B only / operator only
        self.admin_b = User.objects.create_user(email='adminb@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.admin_b, workspace=self.ws_b, role='WORKSPACE_ADMIN')
        self.op_only = User.objects.create_user(email='oponly@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_only, workspace=self.ws_a, role='WORKSPACE_OPERATOR')
        self.conv_a = Conversation.objects.create(
            workspace=self.ws_a, type=Conversation.Type.PLATFORM_SUPPORT,
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject='A')
        self.conv_b = Conversation.objects.create(
            workspace=self.ws_b, type=Conversation.Type.PLATFORM_SUPPORT,
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject='B-private')
        Message.objects.create(
            conversation=self.conv_b, sender=self.admin_b, sender_type='USER',
            content='B secret', client_message_id='b1')


class WorkspaceSupportIsolationTests(_Base, TestCase):
    def setUp(self):
        self.make_world()

    def test_admin_a_operator_b_list_excludes_b(self):
        res = _client(self.mixed).get('/api/v1/support/')
        self.assertEqual(res.status_code, 200)
        ids = {str(c['id']) for c in res.data}
        self.assertEqual(ids, {str(self.conv_a.id)})

    def test_admin_a_operator_b_cannot_read_or_write_b(self):
        c = _client(self.mixed)
        cid = self.conv_b.id
        self.assertEqual(c.get(f'/api/v1/support/{cid}/').status_code, 404)
        self.assertEqual(c.get(f'/api/v1/support/{cid}/messages/').status_code, 404)
        res = c.post(f'/api/v1/support/{cid}/send_message/', {'content': 'x', 'client_message_id': 'zz'}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertEqual(c.post(f'/api/v1/support/{cid}/mark_read/').status_code, 404)
        self.assertFalse(Message.objects.filter(client_message_id='zz').exists())

    def test_no_update_or_delete_even_on_own_conversation(self):
        c = _client(self.mixed)
        for cid in (self.conv_a.id, self.conv_b.id):
            for method in ('patch', 'put', 'delete'):
                res = getattr(c, method)(f'/api/v1/support/{cid}/', {'subject': 'HACKED'}, format='json')
                self.assertIn(res.status_code, (404, 405), f'{method} {cid}')
        self.assertTrue(Conversation.objects.filter(id=self.conv_a.id, subject='A').exists())
        self.assertTrue(Conversation.objects.filter(id=self.conv_b.id, subject='B-private').exists())

    def test_operator_only_user_denied(self):
        c = _client(self.op_only)
        self.assertEqual(c.get('/api/v1/support/').status_code, 403)
        self.assertEqual(c.post('/api/v1/support/', {'subject': 's'}, format='json').status_code, 403)

    def test_unauthenticated_denied(self):
        self.assertIn(APIClient().get('/api/v1/support/').status_code, (401, 403))

    def test_admin_b_sees_only_b(self):
        res = _client(self.admin_b).get('/api/v1/support/')
        self.assertEqual({str(c['id']) for c in res.data}, {str(self.conv_b.id)})

    def test_own_conversation_still_works(self):
        c = _client(self.mixed)
        res = c.post(f'/api/v1/support/{self.conv_a.id}/send_message/', {'content': 'hi', 'client_message_id': 'a-1'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(c.get(f'/api/v1/support/{self.conv_a.id}/messages/').status_code, 200)


class WorkspaceSupportCreateTests(_Base, TestCase):
    def setUp(self):
        self.make_world()

    def test_single_workspace_admin_implied(self):
        res = _client(self.admin_b).post('/api/v1/support/', {'subject': 'Help'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Conversation.objects.get(id=res.data['id']).workspace_id, self.ws_b.id)

    def test_multi_workspace_admin_must_choose(self):
        res = _client(self.multi).post('/api/v1/support/', {'subject': 'Help'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('workspace_id', res.data)

    def test_multi_workspace_admin_choice_is_honoured(self):
        c = _client(self.multi)
        for ws in (self.ws_a, self.ws_b):
            res = c.post('/api/v1/support/', {'subject': 'Help', 'workspace_id': ws.id}, format='json')
            self.assertEqual(res.status_code, 201)
            self.assertEqual(Conversation.objects.get(id=res.data['id']).workspace_id, ws.id)

    def test_cannot_create_in_unrelated_workspace(self):
        res = _client(self.multi).post('/api/v1/support/', {'subject': 'x', 'workspace_id': self.ws_c.id}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Conversation.objects.filter(workspace=self.ws_c).exists())

    def test_cannot_create_where_only_operator(self):
        res = _client(self.mixed).post('/api/v1/support/', {'subject': 'x', 'workspace_id': self.ws_b.id}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Conversation.objects.filter(workspace=self.ws_b, subject='x').count(), 0)

    def test_initial_message_is_persisted(self):
        res = _client(self.admin_b).post('/api/v1/support/', {'subject': 'Help', 'initial_message': 'hello'}, format='json')
        self.assertEqual(res.status_code, 201)
        msgs = Message.objects.filter(conversation_id=res.data['id'])
        self.assertEqual([m.content for m in msgs], ['hello'])
        self.assertEqual(msgs[0].sender_id, self.admin_b.id)

    def test_send_message_requires_client_message_id(self):
        res = _client(self.admin_b).post(f'/api/v1/support/{self.conv_b.id}/send_message/', {'content': 'x'}, format='json')
        self.assertEqual(res.status_code, 400)


class SupportWebSocketIsolationTests(_Base, TransactionTestCase):
    def setUp(self):
        self.make_world()

    async def _connect(self, user, conv):
        from rest_framework_simplejwt.tokens import AccessToken
        token = str(await database_sync_to_async(AccessToken.for_user)(user))
        comm = WebsocketCommunicator(application, f'/ws/dashboard/support/{token}/{conv.id}/')
        connected, _ = await comm.connect()
        if connected:
            await comm.disconnect()
        return connected

    async def test_admin_a_operator_b_cannot_join_b_socket(self):
        self.assertFalse(await self._connect(self.mixed, self.conv_b))

    async def test_admin_a_can_join_own_socket(self):
        self.assertTrue(await self._connect(self.mixed, self.conv_a))

    async def test_unknown_conversation_rejected(self):
        self.conv_b.id = uuid.uuid4()
        self.assertFalse(await self._connect(self.admin_b, self.conv_b))
