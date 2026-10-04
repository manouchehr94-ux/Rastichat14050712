from django.test import TestCase

from conversations.models import Message
from notifications.models import Notification
from .models import AutomationExecution, AutomationEvent, ScheduledAction
from .simulation import simulate
from .tests_base import AutomationTestMixin


class SimulationZeroSideEffectTests(TestCase, AutomationTestMixin):
    """Spec section 12: simulation must evaluate conditions and preview
    actions, but perform NO mutation, send NO message, create NO
    notification, create NO assignment, and schedule NO job.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()
        self.conv.priority = 'HIGH'
        self.conv.save(update_fields=['priority'])

    def _actions(self):
        return [
            {'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
            {'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'سلام {customer_name}'}},
            {'type': 'SEND_NOTIFICATION', 'params': {'title': 'توجه', 'target': 'ASSIGNEE'}},
            {'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 30, 'action': {'type': 'ESCALATE', 'params': {}}}},
        ]

    def test_matched_rule_previews_actions_without_running_them(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        result = simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertTrue(result['matched'])
        self.assertEqual(len(result['would_run_actions']), 4)
        self.assertEqual(result['would_run_actions'][0]['type'], 'SET_PRIORITY')

    def test_no_mutation_of_the_conversation(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')  # still HIGH, never became URGENT

    def test_no_message_sent(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertFalse(Message.objects.filter(conversation=self.conv).exists())

    def test_no_notification_created(self):
        agent = self.make_operator(self.ws)
        self.conv.assigned_to = agent
        self.conv.save(update_fields=['assigned_to', 'priority'])
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertFalse(Notification.objects.exists())

    def test_no_scheduled_action_created(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertFalse(ScheduledAction.objects.exists())

    def test_no_execution_or_event_rows_are_written(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertFalse(AutomationExecution.objects.exists())
        self.assertFalse(AutomationEvent.objects.exists())

    def test_not_matched_rule_shows_no_action_preview(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'LOW'}
        result = simulate(conditions, self._actions(), self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertFalse(result['matched'])
        self.assertEqual(result['would_run_actions'], [])

    def test_condition_trace_reports_matched_leaf(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        result = simulate(conditions, [], self.conv, 'CONVERSATION_PRIORITY_CHANGED')
        self.assertEqual(result['condition_trace']['type'], 'leaf')
        self.assertTrue(result['condition_trace']['result'])
        self.assertEqual(result['condition_trace']['actual'], 'HIGH')

    def test_simulate_without_a_conversation_does_not_crash(self):
        conditions = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        result = simulate(conditions, self._actions(), None, 'CONVERSATION_CREATED')
        self.assertFalse(result['matched'])
