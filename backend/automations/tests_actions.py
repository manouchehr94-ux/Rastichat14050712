import uuid

from django.test import TestCase

from conversations.models import Conversation, Message
from customer_context.models import ConversationTag, Tag
from notifications.models import Notification
from queues.models import Queue
from teams.models import Team, TeamMembership
from .actions import ActionError, execute_action
from .engine import ActionRunContext
from .models import ScheduledAction
from .tests_base import AutomationTestMixin


def _ctx(action_index=0, execution_id=1, rule_id=None):
    # rule_id=None (not a random UUID) since ScheduledAction.rule is a real
    # FK to AutomationRule — these action-level tests don't need a saved
    # rule, and a null rule_id maps cleanly to the nullable FK.
    return ActionRunContext(
        execution_id=execution_id, rule_id=rule_id, correlation_id=uuid.uuid4(), depth=0, action_index=action_index,
        retry_seed=str(execution_id),
    )


class ActionExecutionTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def test_assign_to_agent(self):
        agent = self.make_operator(self.ws)
        result, obj_type, obj_id = execute_action(self.conv, {'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': str(agent.id)}}, _ctx())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, agent.id)
        self.assertEqual(obj_type, 'user')
        self.assertEqual(obj_id, str(agent.id))

    def test_assign_to_agent_rejects_outsider(self):
        other_ws = self.make_workspace()
        outsider = self.make_operator(other_ws)
        with self.assertRaises(ActionError):
            execute_action(self.conv, {'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': str(outsider.id)}}, _ctx())

    def test_assign_to_team(self):
        team = Team.objects.create(workspace=self.ws, name='Sales')
        execute_action(self.conv, {'type': 'ASSIGN_TO_TEAM', 'params': {'team_id': str(team.id)}}, _ctx())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.team_id, team.id)

    def test_unassign(self):
        agent = self.make_operator(self.ws)
        self.conv.assigned_to = agent
        self.conv.save(update_fields=['assigned_to'])
        execute_action(self.conv, {'type': 'UNASSIGN', 'params': {}}, _ctx())
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_to)

    def test_set_priority(self):
        execute_action(self.conv, {'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}}, _ctx())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'URGENT')

    def test_set_status_close_and_reopen(self):
        execute_action(self.conv, {'type': 'SET_STATUS', 'params': {'status': 'CLOSED'}}, _ctx())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, 'CLOSED')
        self.assertIsNotNone(self.conv.closed_at)

        execute_action(self.conv, {'type': 'SET_STATUS', 'params': {'status': 'OPEN'}}, _ctx(action_index=1))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, 'OPEN')

    def test_set_status_intermediate_state_routes_through_shared_service(self):
        """PENDING (and the other intermediate statuses) previously bypassed
        every service via a raw queryset .update() — must now go through
        conv_services.set_status(), which audits and publishes like every
        other status-changing service.
        """
        from audit.models import AuditEvent
        execute_action(self.conv, {'type': 'SET_STATUS', 'params': {'status': 'PENDING'}}, _ctx())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, 'PENDING')
        self.assertTrue(AuditEvent.objects.filter(
            action='conversation_status_changed', target_type='conversation', target_id=str(self.conv.id),
        ).exists())

    def test_reopen_already_open_conversation_is_a_safe_no_op(self):
        result, obj_type, obj_id = execute_action(self.conv, {'type': 'REOPEN_CONVERSATION', 'params': {}}, _ctx())
        self.assertEqual(result, {'skipped': 'not_closed'})

    def test_add_and_remove_tag(self):
        tag = Tag.objects.create(workspace=self.ws, name='vip')
        execute_action(self.conv, {'type': 'ADD_TAG', 'params': {'tag_id': str(tag.id)}}, _ctx())
        self.assertTrue(ConversationTag.objects.filter(conversation=self.conv, tag=tag).exists())
        execute_action(self.conv, {'type': 'REMOVE_TAG', 'params': {'tag_id': str(tag.id)}}, _ctx(action_index=1))
        self.assertFalse(ConversationTag.objects.filter(conversation=self.conv, tag=tag).exists())

    def test_send_customer_message_interpolates_and_persists(self):
        self.visitor.name = 'Sara'
        self.visitor.save(update_fields=['name'])
        result, obj_type, obj_id = execute_action(
            self.conv, {'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'سلام {customer_name}, پیام {unknown_var} باقی می‌ماند.'}}, _ctx(),
        )
        msg = Message.objects.get(id=obj_id)
        self.assertIn('Sara', msg.content)
        self.assertIn('{unknown_var}', msg.content)
        self.assertEqual(msg.sender_type, Message.SenderType.SYSTEM)

    def test_send_customer_message_is_deduplicated(self):
        ctx = _ctx(execution_id=99, action_index=0)
        execute_action(self.conv, {'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'hi'}}, ctx)
        result, _, _ = execute_action(self.conv, {'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'hi'}}, ctx)
        self.assertEqual(result, {'skipped': 'duplicate'})
        self.assertEqual(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.TEXT).count(), 1)

    def test_create_internal_note_is_not_customer_visible_message_type(self):
        result, obj_type, obj_id = execute_action(self.conv, {'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'internal only'}}, _ctx())
        note = Message.objects.get(id=obj_id)
        self.assertEqual(note.message_type, Message.MessageType.INTERNAL_NOTE)

    def test_request_rating_creates_rating_request_message(self):
        result, obj_type, obj_id = execute_action(self.conv, {'type': 'REQUEST_RATING', 'params': {}}, _ctx())
        msg = Message.objects.get(id=obj_id)
        self.assertEqual(msg.message_type, Message.MessageType.RATING_REQUEST)

    def test_send_notification_to_assignee(self):
        agent = self.make_operator(self.ws)
        self.conv.assigned_to = agent
        self.conv.save(update_fields=['assigned_to'])
        result, _, _ = execute_action(self.conv, {'type': 'SEND_NOTIFICATION', 'params': {'title': 'توجه', 'target': 'ASSIGNEE'}}, _ctx())
        self.assertEqual(result['recipient_count'], 1)
        self.assertTrue(Notification.objects.filter(recipient=agent, event_type=Notification.EventType.AUTOMATION_TRIGGERED).exists())

    def test_notify_supervisor(self):
        team = Team.objects.create(workspace=self.ws, name='Sales')
        supervisor = self.make_operator(self.ws)
        TeamMembership.objects.create(team=team, user=supervisor, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        self.conv.team = team
        self.conv.save(update_fields=['team'])
        result, _, _ = execute_action(self.conv, {'type': 'NOTIFY_SUPERVISOR', 'params': {'title': 'نیاز به بررسی'}}, _ctx())
        self.assertEqual(result['recipient_count'], 1)

    def test_schedule_action_creates_pending_row(self):
        result, obj_type, obj_id = execute_action(
            self.conv, {'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 10, 'action': {'type': 'ESCALATE', 'params': {}}}}, _ctx(),
        )
        scheduled = ScheduledAction.objects.get(id=obj_id)
        self.assertEqual(scheduled.status, ScheduledAction.Status.PENDING)
        self.assertEqual(scheduled.action_definition, {'type': 'ESCALATE', 'params': {}})

    def test_schedule_action_is_idempotent_for_the_same_context(self):
        ctx = _ctx(execution_id=7)
        action = {'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 10, 'action': {'type': 'ESCALATE', 'params': {}}}}
        _, _, id1 = execute_action(self.conv, action, ctx)
        _, _, id2 = execute_action(self.conv, action, ctx)
        self.assertEqual(id1, id2)
        self.assertEqual(ScheduledAction.objects.filter(conversation=self.conv).count(), 1)

    def test_cancel_scheduled_action(self):
        execute_action(
            self.conv, {'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 10, 'action': {'type': 'ESCALATE', 'params': {}}}}, _ctx(),
        )
        result, _, _ = execute_action(self.conv, {'type': 'CANCEL_SCHEDULED_ACTION', 'params': {}}, _ctx(action_index=1))
        self.assertEqual(result['cancelled_count'], 1)
        self.assertEqual(ScheduledAction.objects.get(conversation=self.conv).status, ScheduledAction.Status.CANCELLED)

    def test_unknown_action_type_raises_action_error(self):
        with self.assertRaises(ActionError):
            execute_action(self.conv, {'type': 'NOT_A_REAL_ACTION', 'params': {}}, _ctx())

    def test_assign_using_queue_strategy(self):
        team = Team.objects.create(workspace=self.ws, name='Support team')
        queue = Queue.objects.create(workspace=self.ws, team=team, name='Support')
        result, obj_type, obj_id = execute_action(self.conv, {'type': 'ASSIGN_USING_QUEUE_STRATEGY', 'params': {'queue_id': str(queue.id)}}, _ctx())
        self.assertEqual(obj_type, 'conversation')

    def test_every_schema_registered_action_type_has_a_handler(self):
        from .schema import ACTION_PARAM_SPECS
        from .actions import ACTION_HANDLERS
        self.assertEqual(set(ACTION_PARAM_SPECS), set(ACTION_HANDLERS))
