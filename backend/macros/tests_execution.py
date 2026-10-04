import uuid

from rest_framework.test import APITestCase

from conversations.models import Conversation, Message

from . import services
from .models import Macro, MacroExecution
from .tests_base import MacroTestMixin

REFUND_ACTIONS = [
    {'type': 'SEND_REPLY', 'params': {'template': 'سلام {customer_name}، درخواست شما ثبت شد.'}},
    {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
    {'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'نیاز به بررسی مالی'}},
]


class MacroPreviewTests(MacroTestMixin, APITestCase):
    def test_preview_has_no_side_effects(self):
        ws, project, visitor, conv = self.make_full_stack()
        macro = Macro.objects.create(workspace=ws, name='پیش‌نمایش', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=REFUND_ACTIONS)

        result = services.preview_macro(macro, conv)
        self.assertEqual(len(result['actions']), 3)
        self.assertIn('سلام', result['actions'][0]['preview'])

        conv.refresh_from_db()
        self.assertEqual(conv.priority, Conversation.Priority.NORMAL)  # unchanged
        self.assertEqual(Message.objects.filter(conversation=conv).count(), 0)  # no reply/note created
        self.assertEqual(MacroExecution.objects.count(), 0)  # no execution row at all


class MacroExecutionTests(MacroTestMixin, APITestCase):
    def test_confirmation_executes_actions(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='درخواست مرجوعی', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=REFUND_ACTIONS)

        execution = services.execute_macro(macro, conv, admin, str(uuid.uuid4()))
        self.assertEqual(execution.status, MacroExecution.Status.SUCCEEDED)
        self.assertEqual(execution.action_executions.count(), 3)

        conv.refresh_from_db()
        self.assertEqual(conv.priority, Conversation.Priority.HIGH)
        self.assertTrue(Message.objects.filter(conversation=conv, message_type=Message.MessageType.TEXT).exists())
        self.assertTrue(Message.objects.filter(conversation=conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

        macro.refresh_from_db()
        self.assertEqual(macro.execution_count, 1)
        self.assertIsNotNone(macro.last_executed_at)

    def test_double_execution_is_idempotent(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='ماکرو', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=REFUND_ACTIONS)
        key = str(uuid.uuid4())

        services.execute_macro(macro, conv, admin, key)
        services.execute_macro(macro, conv, admin, key)  # same idempotency_key — a double-click

        self.assertEqual(MacroExecution.objects.filter(macro=macro).count(), 1)
        self.assertEqual(Message.objects.filter(conversation=conv, message_type=Message.MessageType.TEXT).count(), 1)
        macro.refresh_from_db()
        self.assertEqual(macro.execution_count, 1)  # not incremented a second time

    def test_execute_api_double_click_prevention(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='ماکرو', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=REFUND_ACTIONS)
        self.login(self.client, admin)
        key = str(uuid.uuid4())
        payload = {'conversation_id': str(conv.id), 'idempotency_key': key}

        res1 = self.client.post(f'/api/v1/macros/{macro.id}/execute/', payload, format='json')
        res2 = self.client.post(f'/api/v1/macros/{macro.id}/execute/', payload, format='json')
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res1.data['id'], res2.data['id'])
        self.assertEqual(Message.objects.filter(conversation=conv, message_type=Message.MessageType.TEXT).count(), 1)

    def test_partial_failure_is_recorded(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        other_ws = self.make_workspace()
        from customer_context.models import Tag
        foreign_tag = Tag.objects.create(workspace=other_ws, name='برچسب خارجی')
        # Bypass schema validation on purpose (a stale reference — e.g. the
        # tag was later deleted/moved) to exercise the ActionError path.
        actions = [
            {'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
            {'type': 'ADD_TAG', 'params': {'tag_id': str(foreign_tag.id)}},
        ]
        macro = Macro.objects.create(workspace=ws, name='ماکرو ناقص', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=actions)

        execution = services.execute_macro(macro, conv, admin, str(uuid.uuid4()))
        self.assertEqual(execution.status, MacroExecution.Status.PARTIALLY_SUCCEEDED)
        statuses = {ae.action_index: ae.status for ae in execution.action_executions.all()}
        self.assertEqual(statuses[0], 'SUCCEEDED')
        self.assertEqual(statuses[1], 'FAILED')

    def test_retry_does_not_duplicate_successful_actions(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        other_ws = self.make_workspace()
        from customer_context.models import Tag
        foreign_tag = Tag.objects.create(workspace=other_ws, name='برچسب خارجی')
        actions = [
            {'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'یادداشت اول'}},
            {'type': 'ADD_TAG', 'params': {'tag_id': str(foreign_tag.id)}},
        ]
        macro = Macro.objects.create(workspace=ws, name='ماکرو تلاش دوباره', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=actions)

        execution = services.execute_macro(macro, conv, admin, str(uuid.uuid4()))
        self.assertEqual(execution.status, MacroExecution.Status.PARTIALLY_SUCCEEDED)
        note_count_after_first = Message.objects.filter(conversation=conv, message_type=Message.MessageType.INTERNAL_NOTE).count()
        self.assertEqual(note_count_after_first, 1)

        # Fix the underlying problem, then retry.
        from customer_context.models import Tag as TagModel
        fixed_tag = TagModel.objects.create(workspace=ws, name='برچسب درست')
        execution.actions_snapshot[1]['params']['tag_id'] = str(fixed_tag.id)
        execution.save(update_fields=['actions_snapshot'])

        execution = services.retry_macro_execution(execution, admin)
        self.assertEqual(execution.status, MacroExecution.Status.SUCCEEDED)
        # The internal note from the FIRST run was never re-created.
        self.assertEqual(Message.objects.filter(conversation=conv, message_type=Message.MessageType.INTERNAL_NOTE).count(), 1)

    def test_inactive_macro_cannot_execute(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        macro = Macro.objects.create(workspace=ws, name='غیرفعال', is_active=False, visibility=Macro.Visibility.WORKSPACE, actions=REFUND_ACTIONS)
        with self.assertRaises(services.MacroError):
            services.execute_macro(macro, conv, admin, str(uuid.uuid4()))
        self.assertEqual(MacroExecution.objects.count(), 0)

    def test_deleted_resource_fails_safely(self):
        ws, project, visitor, conv = self.make_full_stack()
        admin = self.make_admin(ws)
        from teams.models import Team
        team = Team.objects.create(workspace=ws, name='تیم موقت')
        actions = [{'type': 'TRANSFER_TO_TEAM', 'params': {'team_id': str(team.id)}}]
        macro = Macro.objects.create(workspace=ws, name='انتقال', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=actions)
        team.delete()

        execution = services.execute_macro(macro, conv, admin, str(uuid.uuid4()))
        self.assertEqual(execution.status, MacroExecution.Status.FAILED)
        self.assertEqual(execution.action_executions.first().status, 'FAILED')
        conv.refresh_from_db()
        self.assertIsNone(conv.team_id)  # unaffected — the failed action mutated nothing

    def test_execution_history_is_scoped_to_workspace(self):
        ws1, project1, visitor1, conv1 = self.make_full_stack()
        ws2 = self.make_workspace()
        admin1 = self.make_admin(ws1)
        admin2 = self.make_admin(ws2)
        macro1 = Macro.objects.create(workspace=ws1, name='م۱', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[{'type': 'REQUEST_RATING', 'params': {}}])
        services.execute_macro(macro1, conv1, admin1, str(uuid.uuid4()))

        self.login(self.client, admin2)
        res = self.client.get(f'/api/v1/macros/execution-history/?workspace={ws2.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 0)

        self.login(self.client, admin1)
        res = self.client.get(f'/api/v1/macros/execution-history/?workspace={ws1.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 1)

    def test_execution_history_cross_workspace_denied(self):
        ws1, project1, visitor1, conv1 = self.make_full_stack()
        ws2 = self.make_workspace()
        admin1 = self.make_admin(ws1)
        operator2 = self.make_operator(ws2)
        macro1 = Macro.objects.create(workspace=ws1, name='م۱', is_active=True, visibility=Macro.Visibility.WORKSPACE, actions=[{'type': 'REQUEST_RATING', 'params': {}}])
        execution = services.execute_macro(macro1, conv1, admin1, str(uuid.uuid4()))

        self.login(self.client, operator2)
        res = self.client.get(f'/api/v1/macros/executions/{execution.id}/')
        self.assertEqual(res.status_code, 403)
