from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from teams.models import Team, TeamMembership
from conversations.models import Conversation, Message
from .models import Mention, QuickReply
from .services import build_variable_context, resolve_quick_reply


class InternalNoteVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.other_operator = User.objects.create_user(email='op2@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.other_operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project)
        self.session = VisitorSession.objects.create(visitor=self.visitor)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_internal_note_is_invisible_to_the_visitor_widget(self):
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/',
            {'content': 'مشتری کمی عصبانیه، مراقب لحن باش', 'client_message_id': 'note1'}, format='json',
        )
        self.assertEqual(res.status_code, 201)
        widget_res = self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/?session_token={self.session.token}')
        self.assertEqual(widget_res.status_code, 200)
        contents = [m['content'] for m in widget_res.data]
        self.assertNotIn('مشتری کمی عصبانیه، مراقب لحن باش', contents)
        message_types = [m['message_type'] for m in widget_res.data]
        self.assertNotIn('INTERNAL_NOTE', message_types)

    def test_internal_note_is_visible_to_an_authorized_operator(self):
        self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/',
            {'content': 'یادداشت داخلی', 'client_message_id': 'note2'}, format='json',
        )
        list_res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/')
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.data), 1)
        self.assertEqual(list_res.data[0]['content'], 'یادداشت داخلی')

        combined_res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/messages/')
        types = [m['message_type'] for m in combined_res.data]
        self.assertIn('INTERNAL_NOTE', types)

    def test_note_is_server_resolved_to_the_authenticated_sender(self):
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/',
            {'content': 'note', 'client_message_id': 'note3', 'sender': 'someone-else'}, format='json',
        )
        self.assertEqual(res.status_code, 201)
        note = Message.objects.get(client_message_id='note3')
        self.assertEqual(note.sender_id, self.operator.id)

    def test_mentioning_a_colleague_creates_a_mention_and_notification(self):
        from notifications.models import Notification
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/',
            {'content': f'@{self.other_operator.email} لطفا چک کن', 'client_message_id': 'note4', 'mentioned_user_ids': [str(self.other_operator.id)]},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        note = Message.objects.get(client_message_id='note4')
        self.assertTrue(Mention.objects.filter(note=note, mentioned_user=self.other_operator).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.other_operator, event_type=Notification.EventType.MENTIONED).exists())

    def test_cross_workspace_mention_is_rejected(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        outsider = User.objects.create_user(email='outsider@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=outsider, workspace=other_ws, role='WORKSPACE_OPERATOR')

        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/',
            {'content': 'note', 'client_message_id': 'note5', 'mentioned_user_ids': [str(outsider.id)]}, format='json',
        )
        self.assertEqual(res.status_code, 201)
        note = Message.objects.get(client_message_id='note5')
        self.assertFalse(Mention.objects.filter(note=note, mentioned_user=outsider).exists())


class QuickReplyVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.team = Team.objects.create(workspace=self.workspace, name='Sales')
        self.owner = User.objects.create_user(email='owner@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.owner, workspace=self.workspace, role='WORKSPACE_OWNER')
        self.op_in_team = User.objects.create_user(email='in_team@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_in_team, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team, user=self.op_in_team, is_active=True)
        self.op_outside_team = User.objects.create_user(email='outside_team@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_outside_team, workspace=self.workspace, role='WORKSPACE_OPERATOR')

        self.workspace_reply = QuickReply.objects.create(
            workspace=self.workspace, scope=QuickReply.Scope.WORKSPACE, title='Greeting', body='سلام {customer_name}',
            created_by=self.owner,
        )
        self.team_reply = QuickReply.objects.create(
            workspace=self.workspace, scope=QuickReply.Scope.TEAM, team=self.team, title='Sales pitch', body='...',
            created_by=self.owner,
        )
        self.personal_reply = QuickReply.objects.create(
            workspace=self.workspace, scope=QuickReply.Scope.PERSONAL, owner=self.op_in_team, title='My note', body='...',
            created_by=self.op_in_team,
        )

    def _login(self, email):
        res = self.client.post('/api/v1/auth/login/', {'email': email, 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_team_member_sees_workspace_and_team_and_own_personal_replies(self):
        self._login('in_team@test.com')
        res = self.client.get('/api/v1/quick-replies/')
        titles = {r['title'] for r in res.data['results']}
        self.assertEqual(titles, {'Greeting', 'Sales pitch', 'My note'})

    def test_non_team_member_does_not_see_team_or_others_personal_replies(self):
        self._login('outside_team@test.com')
        res = self.client.get('/api/v1/quick-replies/')
        titles = {r['title'] for r in res.data['results']}
        self.assertEqual(titles, {'Greeting'})

    def test_search_filters_by_title_and_body(self):
        self._login('outside_team@test.com')
        res = self.client.get('/api/v1/quick-replies/?search=greeting')
        titles = {r['title'] for r in res.data['results']}
        self.assertEqual(titles, {'Greeting'})

    def test_regular_operator_cannot_create_a_workspace_wide_reply(self):
        self._login('outside_team@test.com')
        res = self.client.post('/api/v1/quick-replies/', {
            'workspace': str(self.workspace.id), 'scope': QuickReply.Scope.WORKSPACE, 'title': 'x', 'body': 'y',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_non_member_cannot_create_a_team_reply(self):
        self._login('outside_team@test.com')
        res = self.client.post('/api/v1/quick-replies/', {
            'workspace': str(self.workspace.id), 'scope': QuickReply.Scope.TEAM, 'team': str(self.team.id), 'title': 'x', 'body': 'y',
        }, format='json')
        self.assertEqual(res.status_code, 403)


class QuickReplyVariableResolutionTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234', display_name='Agent Sara')
        self.visitor = Visitor.objects.create(project=self.project, name='Reza')
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )

    def test_known_variables_are_resolved(self):
        context = build_variable_context(self.conv, self.operator)
        body = resolve_quick_reply('سلام {customer_name}، من {agent_name} هستم از {store_name}', context)
        self.assertIn('Reza', body)
        self.assertIn('Agent Sara', body)
        self.assertIn('Store', body)

    def test_unknown_variable_is_left_untouched_not_evaluated(self):
        context = build_variable_context(self.conv, self.operator)
        body = resolve_quick_reply('hello {secret_internal_field} and {customer_name}', context)
        self.assertIn('{secret_internal_field}', body)  # left as literal text, never resolved
        self.assertIn('Reza', body)
