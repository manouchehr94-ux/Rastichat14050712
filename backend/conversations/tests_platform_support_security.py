"""Regression tests for PlatformSupportViewSet hardening (P0-2).

Audited holes: it was a ModelViewSet (any support agent could PATCH/DELETE a
store's conversation; POST crashed with a 500), `reply` crashed (KeyError ->
500) without `client_message_id` and on duplicates, and `assign` never saved
`assigned_to`.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from conversations.models import Assignment, Conversation, Message
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class PlatformSupportSecurityTests(TestCase):
    def setUp(self):
        self.p1 = Platform.objects.create(name='P1')
        self.p2 = Platform.objects.create(name='P2')
        self.ws1 = Workspace.objects.create(name='W1', platform=self.p1)
        self.ws2 = Workspace.objects.create(name='W2', platform=self.p2)

        def mk(email, platform=None, role=None):
            u = User.objects.create_user(email=email, password='pass1234')
            if platform:
                PlatformMembership.objects.create(user=u, platform=platform, role=role)
            return u

        self.owner = mk('owner@p1.com', self.p1, 'PLATFORM_OWNER')
        self.padmin = mk('padmin@p1.com', self.p1, 'PLATFORM_ADMIN')
        self.agent = mk('agent@p1.com', self.p1, 'PLATFORM_SUPPORT_AGENT')
        self.agent2 = mk('agent2@p2.com', self.p2, 'PLATFORM_SUPPORT_AGENT')
        self.store_admin = mk('sa@w1.com')
        WorkspaceMembership.objects.create(user=self.store_admin, workspace=self.ws1, role='WORKSPACE_ADMIN')
        self.conv = Conversation.objects.create(
            workspace=self.ws1, type=Conversation.Type.PLATFORM_SUPPORT,
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject='W1 ticket')
        Message.objects.create(conversation=self.conv, sender=self.store_admin, sender_type='USER',
                               content='please help', client_message_id='first')
        self.url = f'/api/v1/platform/support/{self.conv.id}/'

    # --- no generic mutation paths ---------------------------------------
    def test_no_generic_mutation_for_any_platform_role(self):
        for user in (self.agent, self.padmin, self.owner):
            c = _client(user)
            for method in ('patch', 'put', 'delete'):
                res = getattr(c, method)(self.url, {'subject': 'EDITED', 'priority': 'URGENT'}, format='json')
                self.assertEqual(res.status_code, 405, f'{user.email} {method}')
            res = c.post('/api/v1/platform/support/', {'subject': 'x'}, format='json')
            self.assertEqual(res.status_code, 405)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.subject, 'W1 ticket')
        self.assertEqual(Conversation.objects.count(), 1)

    # --- reply validation -------------------------------------------------
    def test_reply_without_client_message_id_is_400_not_500(self):
        res = _client(self.agent).post(self.url + 'reply/', {'content': 'hi'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_reply_duplicate_is_409_not_500(self):
        c = _client(self.agent)
        body = {'content': 'hi', 'client_message_id': 'dup'}
        self.assertEqual(c.post(self.url + 'reply/', body, format='json').status_code, 201)
        self.assertEqual(c.post(self.url + 'reply/', body, format='json').status_code, 409)
        self.assertEqual(Message.objects.filter(client_message_id='dup').count(), 1)

    def test_reply_empty_and_oversized_rejected(self):
        c = _client(self.agent)
        self.assertEqual(c.post(self.url + 'reply/', {'content': '  ', 'client_message_id': 'e'}, format='json').status_code, 400)
        self.assertEqual(c.post(self.url + 'reply/', {'content': 'a' * 5001, 'client_message_id': 'o'}, format='json').status_code, 400)

    def test_all_platform_roles_can_reply_and_store_admin_sees_it(self):
        for i, user in enumerate((self.agent, self.padmin, self.owner)):
            res = _client(user).post(self.url + 'reply/', {'content': f'r{i}', 'client_message_id': f'r{i}'}, format='json')
            self.assertEqual(res.status_code, 201)
            self.assertEqual(res.data['content'], f'r{i}')
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, Conversation.Status.WAITING_FOR_WORKSPACE)
        res = _client(self.store_admin).get(f'/api/v1/support/{self.conv.id}/messages/')
        self.assertEqual(len(res.data), 4)

    # --- assign persists --------------------------------------------------
    def test_assign_persists_assignee_and_history(self):
        res = _client(self.agent).post(self.url + 'assign/')
        self.assertEqual(res.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, self.agent.id)
        row = Assignment.objects.get(conversation=self.conv)
        self.assertEqual((row.action, row.assigned_to_id, row.previous_assignee_id), ('CLAIM', self.agent.id, None))

    def test_reassign_records_previous_assignee(self):
        _client(self.agent).post(self.url + 'assign/')
        _client(self.padmin).post(self.url + 'assign/')
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, self.padmin.id)
        last = Assignment.objects.filter(conversation=self.conv).order_by('-created_at', '-id').first()
        self.assertEqual((last.action, last.previous_assignee_id), ('REASSIGN', self.agent.id))
        self.assertEqual(Assignment.objects.filter(conversation=self.conv).count(), 2)

    # --- cross-platform / non-platform users ------------------------------
    def test_other_platform_agent_gets_404_and_no_side_effects(self):
        c = _client(self.agent2)
        self.assertEqual(len(c.get('/api/v1/platform/support/').data), 0)
        self.assertEqual(c.get(self.url).status_code, 404)
        self.assertEqual(c.get(self.url + 'messages/').status_code, 404)
        self.assertEqual(c.post(self.url + 'mark_read/').status_code, 404)
        self.assertEqual(c.post(self.url + 'assign/').status_code, 404)
        self.assertEqual(c.post(self.url + 'reply/', {'content': 'x', 'client_message_id': 'x1'}, format='json').status_code, 404)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_to_id)
        self.assertEqual(Message.objects.filter(conversation=self.conv).count(), 1)
        self.assertEqual(Assignment.objects.count(), 0)

    def test_store_admin_has_no_platform_access(self):
        c = _client(self.store_admin)
        self.assertEqual(c.get('/api/v1/platform/support/').status_code, 403)
        self.assertEqual(c.get(self.url).status_code, 403)
        self.assertEqual(c.post(self.url + 'reply/', {'content': 'x', 'client_message_id': 'x1'}, format='json').status_code, 403)
        self.assertEqual(c.post(self.url + 'assign/').status_code, 403)

    def test_unauthenticated_denied(self):
        self.assertIn(APIClient().get('/api/v1/platform/support/').status_code, (401, 403))

    def test_customer_conversations_never_listed(self):
        Conversation.objects.create(workspace=self.ws1, type=Conversation.Type.CUSTOMER, subject='cust')
        res = _client(self.agent).get('/api/v1/platform/support/')
        self.assertEqual([c['id'] for c in res.data], [str(self.conv.id)])

    def test_mark_read_creates_receipts_for_agent(self):
        res = _client(self.agent).post(self.url + 'mark_read/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.conv.messages.first().receipts.filter(user=self.agent).count(), 1)
