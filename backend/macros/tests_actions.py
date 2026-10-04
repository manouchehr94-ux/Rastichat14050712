import uuid

from rest_framework.test import APITestCase

from accounts.models import OperatorPresence
from conversations.models import Conversation, Message

from . import services
from .models import Macro, MacroExecution
from .tests_base import MacroTestMixin


class MacroActionTests(MacroTestMixin, APITestCase):
    def _exec(self, macro, conv, actor):
        return services.execute_macro(macro, conv, actor, str(uuid.uuid4()))

    def test_reply_action_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='پاسخ', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، متشکریم.'}},
        ])
        execution = self._exec(macro, conv, admin)
        self.assertEqual(execution.status, MacroExecution.Status.SUCCEEDED)
        msg = Message.objects.get(conversation=conv, message_type=Message.MessageType.TEXT)
        self.assertIn('سلام', msg.content)
        self.assertEqual(msg.sender_type, Message.SenderType.USER)

    def test_article_action_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        from knowledge_base import services as kb_services
        article = kb_services.create_article(ws, admin, title='راهنما', body='متن')
        macro = Macro.objects.create(workspace=ws, name='ارسال مقاله', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'SEND_ARTICLE', 'params': {'article_id': str(article.id)}},
        ])
        execution = self._exec(macro, conv, admin)
        self.assertEqual(execution.status, MacroExecution.Status.SUCCEEDED)
        msg = Message.objects.get(conversation=conv, message_type=Message.MessageType.ARTICLE)
        self.assertEqual(msg.metadata['article']['title'], 'راهنما')

    def test_tag_action_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        from customer_context.models import ConversationTag, Tag
        tag = Tag.objects.create(workspace=ws, name='مرجوعی')
        macro = Macro.objects.create(workspace=ws, name='برچسب', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'ADD_TAG', 'params': {'tag_id': str(tag.id)}},
        ])
        self._exec(macro, conv, admin)
        self.assertTrue(ConversationTag.objects.filter(conversation=conv, tag=tag).exists())

    def test_priority_action_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='اولویت', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
        ])
        self._exec(macro, conv, admin)
        conv.refresh_from_db()
        self.assertEqual(conv.priority, Conversation.Priority.URGENT)

    def test_status_action_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='وضعیت', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'SET_STATUS', 'params': {'status': 'WAITING_FOR_WORKSPACE'}},
        ])
        self._exec(macro, conv, admin)
        conv.refresh_from_db()
        self.assertEqual(conv.status, Conversation.Status.WAITING_FOR_WORKSPACE)

    def test_assignment_action_obeys_capacity(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        agent = self.make_operator(ws)
        presence, _ = OperatorPresence.objects.get_or_create(user=agent)
        presence.max_capacity = 1
        presence.save(update_fields=['max_capacity'])
        # Agent is already at capacity with one other active conversation.
        _, other_project, other_visitor, other_conv = self.make_full_stack()
        Conversation.objects.filter(id=other_conv.id).update(workspace=ws, assigned_to=agent)

        macro = Macro.objects.create(workspace=ws, name='واگذاری', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': str(agent.id)}},
        ])
        execution = self._exec(macro, conv, admin)
        self.assertEqual(execution.status, MacroExecution.Status.FAILED)
        conv.refresh_from_db()
        self.assertIsNone(conv.assigned_to_id)

    def test_assignment_action_succeeds_under_capacity(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        agent = self.make_operator(ws)
        macro = Macro.objects.create(workspace=ws, name='واگذاری', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': str(agent.id)}},
        ])
        execution = self._exec(macro, conv, admin)
        self.assertEqual(execution.status, MacroExecution.Status.SUCCEEDED)
        conv.refresh_from_db()
        self.assertEqual(conv.assigned_to_id, agent.id)

    def test_team_transfer_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        team = self.make_team(ws)
        macro = Macro.objects.create(workspace=ws, name='انتقال به تیم فنی', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'TRANSFER_TO_TEAM', 'params': {'team_id': str(team.id)}},
        ])
        self._exec(macro, conv, admin)
        conv.refresh_from_db()
        self.assertEqual(conv.team_id, team.id)

    def test_internal_note_remains_private(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='یادداشت', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'اطلاعات محرمانه داخلی'}},
        ])
        self._exec(macro, conv, admin)
        self.assertTrue(Message.objects.filter(conversation=conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

        from visitors.models import VisitorSession
        session = VisitorSession.objects.create(visitor=visitor)
        res = self.client.get(f'/api/v1/widget/conversations/{conv.id}/messages/?session_token={session.token}')
        self.assertEqual(res.status_code, 200)
        contents = [m['content'] for m in res.data]
        self.assertNotIn('اطلاعات محرمانه داخلی', contents)

    def test_rating_request_works(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='امتیاز', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'REQUEST_RATING', 'params': {}},
        ])
        self._exec(macro, conv, admin)
        self.assertTrue(Message.objects.filter(conversation=conv, message_type=Message.MessageType.RATING_REQUEST).exists())

    def test_close_and_reopen_work(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        close_macro = Macro.objects.create(workspace=ws, name='پایان', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'CLOSE_CONVERSATION', 'params': {}},
        ])
        self._exec(close_macro, conv, admin)
        conv.refresh_from_db()
        self.assertEqual(conv.status, Conversation.Status.CLOSED)

        reopen_macro = Macro.objects.create(workspace=ws, name='بازگشایی', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[
            {'type': 'REOPEN_CONVERSATION', 'params': {}},
        ])
        self._exec(reopen_macro, conv, admin)
        conv.refresh_from_db()
        self.assertEqual(conv.status, Conversation.Status.OPEN)
