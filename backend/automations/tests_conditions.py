from datetime import time

from django.test import TestCase
from django.utils import timezone

from customer_context.models import CustomerProfile
from sla.models import BusinessCalendar, WorkingInterval
from .conditions import ConditionContext, evaluate_condition, normalize_keyword_text
from .events import TriggerEvent
from .tests_base import AutomationTestMixin


class ConditionEvaluationTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()
        self.conv.priority = 'HIGH'
        self.conv.save(update_fields=['priority'])
        self.event = TriggerEvent(event_type='CONVERSATION_PRIORITY_CHANGED', workspace_id=str(self.ws.id))
        self.ctx = ConditionContext(self.event, self.conv)

    def test_empty_tree_always_matches(self):
        self.assertTrue(evaluate_condition({}, self.ctx))

    def test_equals_leaf_matches(self):
        node = {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'}
        self.assertTrue(evaluate_condition(node, self.ctx))
        node['value'] = 'LOW'
        self.assertFalse(evaluate_condition(node, self.ctx))

    def test_all_group_requires_every_child(self):
        node = {'all': [
            {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'},
            {'field': 'conversation.status', 'operator': 'equals', 'value': 'OPEN'},
        ]}
        self.assertTrue(evaluate_condition(node, self.ctx))
        node['all'][1]['value'] = 'CLOSED'
        self.assertFalse(evaluate_condition(node, self.ctx))

    def test_any_group_requires_one_child(self):
        node = {'any': [
            {'field': 'conversation.priority', 'operator': 'equals', 'value': 'LOW'},
            {'field': 'conversation.status', 'operator': 'equals', 'value': 'OPEN'},
        ]}
        self.assertTrue(evaluate_condition(node, self.ctx))

    def test_not_group_inverts(self):
        node = {'not': {'field': 'conversation.priority', 'operator': 'equals', 'value': 'LOW'}}
        self.assertTrue(evaluate_condition(node, self.ctx))

    def test_greater_than_number_operator(self):
        self.conv.rating = 4
        self.conv.save(update_fields=['rating'])
        node = {'field': 'conversation.rating', 'operator': 'greater_than', 'value': 3}
        self.assertTrue(evaluate_condition(node, self.ctx))

    def test_exists_and_not_exists(self):
        node_exists = {'field': 'conversation.assignee_id', 'operator': 'exists'}
        self.assertFalse(evaluate_condition(node_exists, self.ctx))
        node_not_exists = {'field': 'conversation.assignee_id', 'operator': 'not_exists'}
        self.assertTrue(evaluate_condition(node_not_exists, self.ctx))

    def test_unknown_field_defensively_returns_false(self):
        node = {'field': 'conversation.totally_unknown', 'operator': 'equals', 'value': 'x'}
        self.assertFalse(evaluate_condition(node, self.ctx))

    def test_persian_keyword_normalization_for_message_content(self):
        # ي (Arabic yeh) vs ی (Persian yeh), ك (Arabic kaf) vs ک (Persian kaf).
        event = TriggerEvent(
            event_type='CUSTOMER_MESSAGE_CREATED', workspace_id=str(self.ws.id),
            payload={'content': 'سلام علیكم دوست من'},
        )
        ctx = ConditionContext(event, self.conv)
        node = {'field': 'message.content', 'operator': 'contains', 'value': 'علیکم'}
        self.assertTrue(evaluate_condition(node, ctx))

    def test_normalize_keyword_text_handles_none_and_whitespace(self):
        self.assertEqual(normalize_keyword_text(None), '')
        self.assertEqual(normalize_keyword_text('  a   b  '), 'a b')

    def test_customer_metadata_path_lookup(self):
        CustomerProfile.objects.create(visitor=self.visitor, workspace=self.ws, metadata={'vip_level': 'gold'})
        node = {'field': 'customer.metadata', 'operator': 'equals', 'value': 'gold', 'path': 'vip_level'}
        self.assertTrue(evaluate_condition(node, self.ctx))
        node['path'] = 'missing_key'
        self.assertFalse(evaluate_condition(node, self.ctx))

    def test_business_hours_true_with_no_calendar_configured(self):
        node = {'field': 'operational.business_hours', 'operator': 'is_true'}
        self.assertTrue(evaluate_condition(node, self.ctx))

    def test_business_hours_respects_configured_calendar(self):
        calendar = BusinessCalendar.objects.create(workspace=self.ws, name='Default', timezone='UTC')
        now = timezone.now()
        # A single working window covering the whole day, so "now" is
        # deterministically inside it regardless of when the test runs.
        WorkingInterval.objects.create(calendar=calendar, weekday=now.weekday(), start_time=time(0, 0), end_time=time(23, 59))
        node = {'field': 'operational.business_hours', 'operator': 'is_true'}
        self.assertTrue(evaluate_condition(node, self.ctx))
