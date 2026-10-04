import uuid

from django.test import TestCase
from django.utils import timezone

from .engine import MAX_ACTIONS_PER_CORRELATION, process_event
from .events import MAX_AUTOMATION_DEPTH, TriggerEvent
from .models import (
    AutomationActionExecution, AutomationCorrelationCounter, AutomationEvent, AutomationExecution, AutomationRule,
)
from .tests_base import AutomationTestMixin


class EngineOrchestrationTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _rule(self, **kwargs):
        defaults = dict(
            workspace=self.ws, is_active=True, trigger_type=AutomationRule.Trigger.CONVERSATION_CREATED,
            conditions={}, actions=[], priority=100,
        )
        defaults.update(kwargs)
        return AutomationRule.objects.create(**defaults)

    def _event(self, event_type='CONVERSATION_CREATED', **kwargs):
        return TriggerEvent(event_type=event_type, workspace_id=str(self.ws.id), conversation_id=str(self.conv.id), **kwargs)

    def test_matching_rule_executes_its_action(self):
        self._rule(name='r1', actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        process_event(self._event())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')

    def test_non_matching_condition_is_recorded_not_matched(self):
        self._rule(name='r1', conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'URGENT'})
        event = self._event()
        process_event(event)
        execution = AutomationExecution.objects.get(event_id=event.event_id)
        self.assertEqual(execution.status, AutomationExecution.Status.NOT_MATCHED)

    def test_inactive_rule_is_never_evaluated(self):
        self._rule(name='inactive', is_active=False, actions=[{'type': 'ESCALATE', 'params': {}}])
        process_event(self._event())
        self.assertFalse(AutomationExecution.objects.exists())

    def test_duplicate_event_id_is_processed_once(self):
        self._rule(name='r1', actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        event = self._event()
        process_event(event)
        process_event(event)  # same event_id — must be a no-op the second time
        self.assertEqual(AutomationExecution.objects.filter(event_id=event.event_id).count(), 1)

    def test_execution_order_is_priority_then_created_at(self):
        low_priority_rule = self._rule(name='runs second', priority=200, actions=[{'type': 'ESCALATE', 'params': {}}])
        high_priority_rule = self._rule(name='runs first', priority=50, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        event = self._event()
        process_event(event)
        executions = list(AutomationExecution.objects.filter(event_id=event.event_id).order_by('created_at'))
        self.assertEqual([e.rule_id for e in executions], [high_priority_rule.id, low_priority_rule.id])

    def test_stop_processing_halts_lower_priority_rules(self):
        self._rule(name='stopper', priority=10, stop_processing=True, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        never_runs = self._rule(name='never runs', priority=20, actions=[{'type': 'ESCALATE', 'params': {}}])
        process_event(self._event())
        self.assertFalse(AutomationExecution.objects.filter(rule=never_runs).exists())

    def test_without_stop_processing_lower_priority_rule_still_runs(self):
        self._rule(name='first', priority=10, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        second = self._rule(name='second', priority=20, actions=[{'type': 'ESCALATE', 'params': {}}])
        process_event(self._event())
        self.assertTrue(AutomationExecution.objects.filter(rule=second, status=AutomationExecution.Status.SUCCEEDED).exists())

    def test_valid_from_in_the_future_skips_rule(self):
        self._rule(name='not yet', valid_from=timezone.now() + timezone.timedelta(days=1), actions=[{'type': 'ESCALATE', 'params': {}}])
        process_event(self._event())
        self.assertFalse(AutomationExecution.objects.exists())

    def test_valid_until_in_the_past_skips_rule(self):
        self._rule(name='expired', valid_until=timezone.now() - timezone.timedelta(days=1), actions=[{'type': 'ESCALATE', 'params': {}}])
        process_event(self._event())
        self.assertFalse(AutomationExecution.objects.exists())

    def test_action_failure_is_recorded_as_failed_execution(self):
        self._rule(name='bad ref', actions=[{'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': '00000000-0000-0000-0000-000000000000'}}])
        event = self._event()
        process_event(event)
        execution = AutomationExecution.objects.get(event_id=event.event_id)
        self.assertEqual(execution.status, AutomationExecution.Status.FAILED)
        self.assertTrue(execution.action_executions.filter(status='FAILED').exists())

    def test_partially_succeeded_when_some_actions_fail(self):
        self._rule(name='mixed', actions=[
            {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
            {'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': '00000000-0000-0000-0000-000000000000'}},
        ])
        event = self._event()
        process_event(event)
        execution = AutomationExecution.objects.get(event_id=event.event_id)
        self.assertEqual(execution.status, AutomationExecution.Status.PARTIALLY_SUCCEEDED)

    def test_no_conversation_still_records_a_matched_execution(self):
        self._rule(name='r1', actions=[{'type': 'ESCALATE', 'params': {}}])
        orphan_event = TriggerEvent(event_type='CONVERSATION_CREATED', workspace_id=str(self.ws.id), conversation_id=None)
        process_event(orphan_event)
        execution = AutomationExecution.objects.get(event_id=orphan_event.event_id)
        self.assertEqual(execution.status, AutomationExecution.Status.MATCHED)
        self.assertFalse(execution.action_executions.exists())

    def test_loop_protection_stops_a_two_rule_ping_pong(self):
        self.conv.priority = 'HIGH'
        self.conv.save(update_fields=['priority'])
        rule_a = self._rule(
            name='A', trigger_type=AutomationRule.Trigger.CONVERSATION_PRIORITY_CHANGED,
            conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'},
            actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}}],
        )
        rule_b = self._rule(
            name='B', trigger_type=AutomationRule.Trigger.CONVERSATION_PRIORITY_CHANGED,
            conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'URGENT'},
            actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
        )
        # Rule A's action calls conversations.services.set_priority(), which
        # itself calls publish_event() — deferred via transaction.on_commit.
        # captureOnCommitCallbacks(execute=True) is what actually runs those
        # deferred callbacks (and any further ones they themselves queue,
        # e.g. rule B's own publish_event) inside a TestCase, where a real
        # commit never happens.
        with self.captureOnCommitCallbacks(execute=True):
            process_event(self._event(event_type='CONVERSATION_PRIORITY_CHANGED'))
        self.conv.refresh_from_db()
        # Each rule may only run once per correlation chain — the chain
        # terminates instead of ping-ponging forever.
        self.assertEqual(AutomationExecution.objects.filter(rule=rule_a, status='SUCCEEDED').count(), 1)
        self.assertEqual(AutomationExecution.objects.filter(rule=rule_b, status='SUCCEEDED').count(), 1)
        self.assertTrue(AutomationExecution.objects.filter(rule=rule_a, status='SKIPPED_LOOP').exists())

    def test_same_rule_never_reruns_within_one_correlation(self):
        rule = self._rule(name='self-trigger', actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        event = self._event()
        process_event(event)
        # Simulate a second, distinct event that inherits the same
        # correlation_id (as would happen if a domain service published
        # another event from inside this rule's own action).
        chained_event = TriggerEvent(
            event_type='CONVERSATION_CREATED', workspace_id=str(self.ws.id), conversation_id=str(self.conv.id),
            correlation_id=event.correlation_id, depth=1,
        )
        process_event(chained_event)
        self.assertTrue(AutomationExecution.objects.filter(rule=rule, status='SKIPPED_LOOP', event_id=chained_event.event_id).exists())

    def test_max_depth_bounds_processing(self):
        self._rule(name='r1', actions=[{'type': 'ESCALATE', 'params': {}}])
        too_deep_event = TriggerEvent(
            event_type='CONVERSATION_CREATED', workspace_id=str(self.ws.id), conversation_id=str(self.conv.id),
            depth=MAX_AUTOMATION_DEPTH + 1,
        )
        process_event(too_deep_event)
        # No rule ran (bounded), but the bound itself must leave an
        # observable, explicit trace rather than silently doing nothing.
        executions = AutomationExecution.objects.filter(event_id=too_deep_event.event_id)
        self.assertEqual(executions.count(), 1)
        skip = executions.get()
        self.assertEqual(skip.status, AutomationExecution.Status.SKIPPED_LOOP)
        self.assertIsNone(skip.rule)
        self.assertIn('MAX_AUTOMATION_DEPTH', skip.error_summary)

    def test_max_actions_per_correlation_bounds_processing(self):
        self._rule(name='r1', actions=[{'type': 'ESCALATE', 'params': {}}])
        correlation_id = uuid.uuid4()
        # Pre-populate the correlation with enough action-execution rows to
        # hit MAX_ACTIONS_PER_CORRELATION before this event is processed.
        parent_event = AutomationEvent.objects.create(
            event_id=uuid.uuid4(), event_type='SEED', workspace=self.ws, correlation_id=correlation_id,
        )
        execution = AutomationExecution.objects.create(
            workspace=self.ws, trigger_type='SEED', event_id=parent_event.event_id, correlation_id=correlation_id,
            conversation=self.conv, status=AutomationExecution.Status.SUCCEEDED,
        )
        AutomationActionExecution.objects.bulk_create([
            AutomationActionExecution(execution=execution, action_index=i, action_type='ESCALATE', status='SUCCEEDED')
            for i in range(MAX_ACTIONS_PER_CORRELATION)
        ])
        # The hard, concurrency-safe bound now lives in
        # AutomationCorrelationCounter (reserved via reserve_action_slot),
        # not a live .count() of AutomationActionExecution rows — the
        # correlation's reservation ledger must reflect the same "already
        # at the cap" state the bulk-created rows above represent.
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION,
        )
        bounded_event = TriggerEvent(
            event_type='CONVERSATION_CREATED', workspace_id=str(self.ws.id), conversation_id=str(self.conv.id),
            correlation_id=correlation_id, depth=1,
        )
        process_event(bounded_event)
        executions = AutomationExecution.objects.filter(event_id=bounded_event.event_id)
        self.assertEqual(executions.count(), 1)
        skip = executions.get()
        self.assertEqual(skip.status, AutomationExecution.Status.SKIPPED_LOOP)
        self.assertIsNone(skip.rule)
        self.assertIn('MAX_ACTIONS_PER_CORRELATION', skip.error_summary)

    def test_action_limit_reached_mid_rule_truncates_remaining_actions(self):
        """Two actions in one rule; the correlation is exactly one slot
        short of the cap when the rule starts — the first action must still
        run for real (it wins the last slot), the second must be reserved
        via the SAME per-action reserve_action_slot() call and lose,
        recorded as a SKIPPED AutomationActionExecution rather than
        silently vanishing or running unbounded.
        """
        self._rule(name='two-actions', actions=[
            {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
            {'type': 'ADD_TAG', 'params': {'tag_id': str(uuid.uuid4())}},  # never reached — would fail resolution anyway
        ])
        correlation_id = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION - 1,
        )
        event = TriggerEvent(
            event_type='CONVERSATION_CREATED', workspace_id=str(self.ws.id), conversation_id=str(self.conv.id),
            correlation_id=correlation_id, depth=0,
        )
        process_event(event)

        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')  # the first action actually ran

        execution = AutomationExecution.objects.get(event_id=event.event_id)
        self.assertEqual(execution.status, AutomationExecution.Status.PARTIALLY_SUCCEEDED)
        action_execs = list(execution.action_executions.order_by('action_index'))
        self.assertEqual(len(action_execs), 2)
        self.assertEqual(action_execs[0].status, AutomationActionExecution.Status.SUCCEEDED)
        self.assertEqual(action_execs[1].status, AutomationActionExecution.Status.SKIPPED)
        self.assertIn('MAX_ACTIONS_PER_CORRELATION', action_execs[1].error_summary)

        counter = AutomationCorrelationCounter.objects.get(correlation_id=correlation_id)
        self.assertEqual(counter.actions_reserved, MAX_ACTIONS_PER_CORRELATION)  # exactly the cap, never over
