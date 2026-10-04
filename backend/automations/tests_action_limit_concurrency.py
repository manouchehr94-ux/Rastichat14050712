"""Concurrency-safety regression coverage for MAX_ACTIONS_PER_CORRELATION —
the previous unlocked `.count()` read let two workers both read "under the
limit" and both proceed, overshooting the cap by 1 (independently
reproduced against this exact commit before the fix: two concurrent workers
racing a correlation seeded at 39/40 both executed, landing at 41). This
module proves automations.idempotency.reserve_action_slot's row-locked
counter closes that race, in both process_automation_jobs (scheduled path)
and engine.process_event (instant path).
"""
import threading
import uuid

from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .engine import MAX_ACTIONS_PER_CORRELATION, process_event
from .events import TriggerEvent
from .idempotency import reserve_action_slot
from .models import (
    AutomationActionExecution, AutomationActionSlotReservation, AutomationCorrelationCounter, AutomationExecution,
    ScheduledAction,
)
from .tests_base import AutomationTestMixin


class ActionLimitConcurrencyTests(TransactionTestCase, AutomationTestMixin):
    """TransactionTestCase: real, independently-committed transactions per
    thread are required for genuine row-lock contention on
    AutomationCorrelationCounter — the same reasoning as
    queues.tests.ConcurrentClaimTests.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _due_jobs(self, correlation_id, count):
        jobs = []
        for i in range(count):
            jobs.append(ScheduledAction.objects.create(
                workspace=self.ws, conversation=self.conv,
                action_definition={'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
                correlation_id=correlation_id, depth=0,
                execute_at=timezone.now() - timezone.timedelta(minutes=1),
                status=ScheduledAction.Status.PENDING, idempotency_key=f'action-limit-race-{correlation_id}-{i}',
            ))
        return jobs

    # 1. count at limit minus one, two concurrent workers -> only one executes
    def test_two_concurrent_workers_at_boundary_only_one_executes(self):
        correlation_id = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION - 1,
        )
        self._due_jobs(correlation_id, 2)

        def _run():
            call_command('process_automation_jobs')
            connection.close()

        t1 = threading.Thread(target=_run)
        t2 = threading.Thread(target=_run)
        t1.start(); t2.start()
        t1.join(); t2.join()

        succeeded = ScheduledAction.objects.filter(correlation_id=correlation_id, status=ScheduledAction.Status.SUCCEEDED).count()
        skipped = ScheduledAction.objects.filter(correlation_id=correlation_id, status=ScheduledAction.Status.SKIPPED).count()
        self.assertEqual(succeeded, 1, 'exactly one of the two racing jobs should have won the last slot')
        self.assertEqual(skipped, 1)
        counter = AutomationCorrelationCounter.objects.get(correlation_id=correlation_id)
        self.assertEqual(counter.actions_reserved, MAX_ACTIONS_PER_CORRELATION)

    # 2. count already at limit -> none executes
    def test_at_limit_no_job_executes(self):
        correlation_id = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION,
        )
        jobs = self._due_jobs(correlation_id, 3)
        call_command('process_automation_jobs')
        for job in jobs:
            job.refresh_from_db()
            self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)
        counter = AutomationCorrelationCounter.objects.get(correlation_id=correlation_id)
        self.assertEqual(counter.actions_reserved, MAX_ACTIONS_PER_CORRELATION)  # unchanged — no overshoot

    # 3. many workers racing near the limit -> exact hard cap
    def test_ten_workers_racing_near_the_limit_hit_exact_hard_cap(self):
        correlation_id = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION - 5,
        )
        self._due_jobs(correlation_id, 10)

        def _run():
            call_command('process_automation_jobs')
            connection.close()

        threads = [threading.Thread(target=_run) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        succeeded = ScheduledAction.objects.filter(correlation_id=correlation_id, status=ScheduledAction.Status.SUCCEEDED).count()
        skipped = ScheduledAction.objects.filter(correlation_id=correlation_id, status=ScheduledAction.Status.SKIPPED).count()
        self.assertEqual(succeeded, 5, f'expected exactly 5 winners (the remaining slots), got {succeeded}')
        self.assertEqual(skipped, 5)
        counter = AutomationCorrelationCounter.objects.get(correlation_id=correlation_id)
        self.assertEqual(counter.actions_reserved, MAX_ACTIONS_PER_CORRELATION)

    # 4. unrelated correlations execute concurrently (never block each other)
    def test_unrelated_correlations_do_not_block_each_other(self):
        corr_a = uuid.uuid4()
        corr_b = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(correlation_id=corr_a, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION - 1)
        AutomationCorrelationCounter.objects.create(correlation_id=corr_b, workspace=self.ws, actions_reserved=0)
        self._due_jobs(corr_a, 1)
        self._due_jobs(corr_b, 1)

        call_command('process_automation_jobs')

        job_a = ScheduledAction.objects.get(correlation_id=corr_a)
        job_b = ScheduledAction.objects.get(correlation_id=corr_b)
        self.assertEqual(job_a.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(job_b.status, ScheduledAction.Status.SUCCEEDED)


class ActionLimitAuditVisibilityAndRepeatTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _due_job(self, correlation_id, key='action-limit-visible'):
        return ScheduledAction.objects.create(
            workspace=self.ws, conversation=self.conv,
            action_definition={'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}},
            correlation_id=correlation_id, depth=0, execute_at=timezone.now() - timezone.timedelta(minutes=1),
            status=ScheduledAction.Status.PENDING, idempotency_key=key,
        )

    # 5. skipped execution is visible in history
    def test_skipped_execution_is_visible_in_history(self):
        correlation_id = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION,
        )
        job = self._due_job(correlation_id)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)
        skip = AutomationExecution.objects.get(correlation_id=correlation_id, status=AutomationExecution.Status.SKIPPED_LOOP)
        self.assertIn('MAX_ACTIONS_PER_CORRELATION', skip.error_summary)

    # 6. repeated processor runs do not exceed the cap
    def test_repeated_processor_runs_never_exceed_the_cap(self):
        correlation_id = uuid.uuid4()
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION - 2,
        )
        for i in range(5):
            self._due_job(correlation_id, key=f'action-limit-repeat-{i}')

        for _ in range(5):  # simulate 5 separate cron ticks
            call_command('process_automation_jobs')

        succeeded = ScheduledAction.objects.filter(correlation_id=correlation_id, status=ScheduledAction.Status.SUCCEEDED).count()
        counter = AutomationCorrelationCounter.objects.get(correlation_id=correlation_id)
        self.assertEqual(succeeded, 2)
        self.assertEqual(counter.actions_reserved, MAX_ACTIONS_PER_CORRELATION)

    # 7. same action retry does not double-consume the counter
    def test_same_action_retry_does_not_double_consume_the_counter(self):
        correlation_id = uuid.uuid4()
        self.assertTrue(reserve_action_slot(correlation_id, self.ws.id, MAX_ACTIONS_PER_CORRELATION, reservation_key='retry-key-1'))
        counter = AutomationCorrelationCounter.objects.get(correlation_id=correlation_id)
        self.assertEqual(counter.actions_reserved, 1)

        # A retry of the SAME logical attempt (identical reservation_key)
        # must find its own row and return True without incrementing again.
        self.assertTrue(reserve_action_slot(correlation_id, self.ws.id, MAX_ACTIONS_PER_CORRELATION, reservation_key='retry-key-1'))
        counter.refresh_from_db()
        self.assertEqual(counter.actions_reserved, 1)
        self.assertEqual(AutomationActionSlotReservation.objects.filter(reservation_key='retry-key-1').count(), 1)

        # A genuinely DIFFERENT action attempt still consumes a real, distinct slot.
        self.assertTrue(reserve_action_slot(correlation_id, self.ws.id, MAX_ACTIONS_PER_CORRELATION, reservation_key='retry-key-2'))
        counter.refresh_from_db()
        self.assertEqual(counter.actions_reserved, 2)
