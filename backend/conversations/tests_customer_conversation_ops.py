"""Regression tests for customer-conversation operator operations (P0-3).

Audited holes: CustomerConversationViewSet was a ModelViewSet, so any plain
operator could DELETE a customer conversation (204) and PATCH `team`/`queue`
to another workspace's team/queue (FK crossed the tenant boundary), bypassing
the audited transfer/priority actions.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from conversations.models import Conversation, Message
from platforms.models import Platform
from projects.models import Project
from queues.models import Queue
from teams.models import Team
from visitors.models import Visitor
from workspaces.models import Workspace, WorkspaceMembership


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class CustomerConversationOpsTests(TestCase):
    def setUp(self):
        platform = Platform.objects.create(name='P')
        self.ws_a = Workspace.objects.create(name='A', platform=platform)
        self.ws_b = Workspace.objects.create(name='B', platform=platform)
        self.op_a = User.objects.create_user(email='opa@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_a, workspace=self.ws_a, role='WORKSPACE_OPERATOR')
        self.op_b = User.objects.create_user(email='opb@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_b, workspace=self.ws_b, role='WORKSPACE_OPERATOR')
        self.team_a = Team.objects.create(workspace=self.ws_a, name='tA')
        self.team_b = Team.objects.create(workspace=self.ws_b, name='tB')
        self.queue_b = Queue.objects.create(workspace=self.ws_b, team=self.team_b, name='qB')
        visitor = Visitor.objects.create(project=Project.objects.create(name='pa', workspace=self.ws_a), name='v')
        self.conv = Conversation.objects.create(workspace=self.ws_a, type='CUSTOMER', visitor=visitor, subject='s0', category='c0')
        Message.objects.create(conversation=self.conv, sender_type='VISITOR', sender_visitor=visitor,
                               content='hello', client_message_id='m1')
        self.url = f'/api/v1/conversations/customer/{self.conv.id}/'

    def test_operator_cannot_delete(self):
        res = _client(self.op_a).delete(self.url)
        self.assertEqual(res.status_code, 405)
        self.assertTrue(Conversation.objects.filter(id=self.conv.id).exists())
        self.assertEqual(self.conv.messages.count(), 1)

    def test_no_put_and_no_create(self):
        c = _client(self.op_a)
        self.assertEqual(c.put(self.url, {'category': 'x'}, format='json').status_code, 405)
        self.assertEqual(c.post('/api/v1/conversations/customer/', {'subject': 'x'}, format='json').status_code, 405)
        self.assertEqual(Conversation.objects.count(), 1)

    def test_editable_fields_still_patchable(self):
        res = _client(self.op_a).patch(self.url, {'category': 'billing', 'subject': 's1', 'notes': 'n'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['category'], 'billing')
        self.conv.refresh_from_db()
        self.assertEqual((self.conv.category, self.conv.subject, self.conv.notes), ('billing', 's1', 'n'))

    def test_cross_workspace_team_and_queue_rejected(self):
        res = _client(self.op_a).patch(self.url, {'team': self.team_b.id, 'queue': str(self.queue_b.id)}, format='json')
        self.assertEqual(res.status_code, 400)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.team_id)
        self.assertIsNone(self.conv.queue_id)

    def test_routing_and_ownership_fields_not_patchable_even_same_workspace(self):
        for body in ({'team': self.team_a.id}, {'priority': 'URGENT'}, {'status': 'CLOSED'},
                     {'assigned_to': self.op_a.id}, {'workspace': self.ws_b.id}, {'rating': 5}):
            res = _client(self.op_a).patch(self.url, body, format='json')
            self.assertEqual(res.status_code, 400, body)
        self.conv.refresh_from_db()
        self.assertEqual((self.conv.priority, self.conv.status, self.conv.workspace_id, self.conv.rating),
                         ('NORMAL', 'OPEN', self.ws_a.id, None))
        self.assertIsNone(self.conv.team_id)
        self.assertIsNone(self.conv.assigned_to_id)

    def test_other_workspace_operator_cannot_touch(self):
        c = _client(self.op_b)
        self.assertEqual(c.patch(self.url, {'category': 'x'}, format='json').status_code, 404)
        self.assertEqual(c.delete(self.url).status_code, 405)
        self.assertEqual(c.get(self.url).status_code, 404)
        self.assertEqual(len(c.get('/api/v1/conversations/customer/').data), 0)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.category, 'c0')

    def test_dedicated_actions_reject_cross_workspace_targets(self):
        c = _client(self.op_a)
        res = c.post(self.url + 'transfer/', {'team_id': str(self.team_b.id)}, format='json')
        self.assertEqual(res.status_code, 404)
        res = c.post(self.url + 'assign/', {'operator_id': self.op_b.id}, format='json')
        self.assertEqual(res.status_code, 404)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.team_id)
        self.assertIsNone(self.conv.assigned_to_id)

    def test_list_and_retrieve_unchanged(self):
        c = _client(self.op_a)
        self.assertEqual([x['id'] for x in c.get('/api/v1/conversations/customer/').data], [str(self.conv.id)])
        self.assertEqual(c.get(self.url).status_code, 200)

    def test_unauthenticated_denied(self):
        self.assertIn(APIClient().patch(self.url, {'category': 'x'}, format='json').status_code, (401, 403))
        self.assertIn(APIClient().delete(self.url).status_code, (401, 403))
