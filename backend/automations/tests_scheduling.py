import threading
import uuid

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .engine import MAX_ACTIONS_PER_CORRELATION
from .events import MAX_AUTOMATION_DEPTH
from .models import (
    AutomationActionExecution, AutomationCorrelationCounter, AutomationExecution, AutomationRule, ScheduledAction,
)
from .scheduling import schedule_action
from .tests_base import AutomationTestMixin


class ScheduleActionCreationTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def test_creates_a_pending_row(self):
        scheduled = schedule_action(
            workspace=self.ws, rule_id=None, conversation=self.conv,
            action_definition={'type': 'ESCALATE', 'params': {}}, execute_at=timezone.now() + timezone.timedelta(minutes=5),
            correlation_id=uuid.uuid4(), depth=0, idempotency_seed='seed-1',
        )
        self.assertEqual(scheduled.status, ScheduledAction.Status.PENDING)

    def test_same_seed_is_idempotent(self):
        kwargs = dict(
            workspace=self.ws, rule_id=None, conversation=self.conv,
            action_definition={'type': 'ESCALATE', 'params': {}}, execute_at=timezone.now() + timezone.timedelta(minutes=5),
            correlation_id=uuid.uuid4(), depth=0, idempotency_seed='same-seed',
        )
        first = schedule_action(**kwargs)
        second = schedule_action(**kwargs)
        self.assertEqual(first.id, second.id)
        self.assertEqual(ScheduledAction.objects.count(), 1)

    def test_different_seed_creates_a_distinct_row(self):
        base = dict(
            workspace=self.ws, rule_id=None, conversation=self.conv,
            action_definition={'type': 'ESCALATE', 'params': {}}, execute_at=timezone.now() + timezone.timedelta(minutes=5),
            correlation_id=uuid.uuid4(), depth=0,
        )
        schedule_action(idempotency_seed='seed-a', **base)
        schedule_action(idempotency_seed='seed-b', **base)
        self.assertEqual(ScheduledAction.objects.count(), 2)


class ProcessAutomationJobsCommandTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _due_job(self, action_definition, max_attempts=3, execute_at=None):
        return ScheduledAction.objects.create(
            workspace=self.ws, conversation=self.conv, action_definition=action_definition,
            correlation_id=uuid.uuid4(), depth=0, execute_at=execute_at or (timezone.now() - timezone.timedelta(minutes=1)),
            status=ScheduledAction.Status.PENDING, max_attempts=max_attempts,
        )

    def test_executes_a_due_job_and_marks_it_succeeded(self):
        job = self._due_job({'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}})
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.conv.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertIsNotNone(job.executed_at)
        self.assertEqual(self.conv.priority, 'URGENT')
        self.assertTrue(AutomationExecution.objects.filter(
            trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED, status=AutomationExecution.Status.SUCCEEDED,
        ).exists())

    def test_future_job_is_left_untouched(self):
        job = self._due_job({'type': 'ESCALATE', 'params': {}}, execute_at=timezone.now() + timezone.timedelta(hours=1))
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)
        self.assertEqual(job.attempts, 0)

    def test_already_running_job_is_not_reclaimed(self):
        job = self._due_job({'type': 'ESCALATE', 'params': {}})
        job.status = ScheduledAction.Status.RUNNING
        job.save(update_fields=['status'])
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.RUNNING)  # untouched, not re-run

    def test_failed_job_retries_until_max_attempts_then_permanently_fails(self):
        job = self._due_job({'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': '00000000-0000-0000-0000-000000000000'}}, max_attempts=2)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)  # 1st attempt failed, retryable
        self.assertEqual(job.attempts, 1)

        job.execute_at = timezone.now() - timezone.timedelta(minutes=1)
        job.save(update_fields=['execute_at'])
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.FAILED)  # 2nd attempt exhausted max_attempts
        self.assertEqual(job.attempts, 2)

    def test_job_with_no_conversation_is_skipped_not_crashed(self):
        job = ScheduledAction.objects.create(
            workspace=self.ws, conversation=None, action_definition={'type': 'ESCALATE', 'params': {}},
            correlation_id=uuid.uuid4(), depth=0, execute_at=timezone.now() - timezone.timedelta(minutes=1),
            status=ScheduledAction.Status.PENDING,
        )
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)

    def test_cancelled_job_is_never_picked_up(self):
        job = self._due_job({'type': 'ESCALATE', 'params': {}})
        job.status = ScheduledAction.Status.CANCELLED
        job.save(update_fields=['status'])
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.CANCELLED)
        self.assertEqual(job.attempts, 0)

    def test_running_command_twice_does_not_double_execute(self):
        job = self._due_job({'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}})
        call_command('process_automation_jobs')
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(
            AutomationExecution.objects.filter(trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED).count(), 1,
        )


class StaleJobRecoveryTests(TestCase, AutomationTestMixin):
    """Crash-point coverage for a worker that claims a job (marks it RUNNING)
    and then dies before ever reaching _finish()/_fail() — killed process,
    OOM, host reboot. Every crash point below leaves the row in exactly the
    same DB state (RUNNING, a real locked_at/locked_by, attempts already
    incremented once by the original claim) since the crash, by definition,
    happens somewhere the command never gets to write again.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _stuck_running_job(self, action_definition=None, attempts=1, max_attempts=3, locked_minutes_ago=10, rule=None, due=False):
        # Not due by default: recovery runs before the normal due-job claim
        # pass in the same command invocation, so a due job would be
        # recovered to PENDING *and* immediately re-claimed/executed in that
        # same run — correct end-to-end behavior, but it would make the
        # intermediate "just recovered" PENDING state unobservable for tests
        # that specifically want to assert on it. Tests that want the full
        # recover-then-execute round trip pass due=True (or advance
        # execute_at themselves, as test_crash_point_2 does for its retry).
        execute_at = timezone.now() - timezone.timedelta(minutes=30) if due else timezone.now() + timezone.timedelta(hours=1)
        job = ScheduledAction.objects.create(
            workspace=self.ws, rule=rule, conversation=self.conv,
            action_definition=action_definition or {'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
            correlation_id=uuid.uuid4(), depth=0, execute_at=execute_at,
            status=ScheduledAction.Status.RUNNING, attempts=attempts, max_attempts=max_attempts,
            locked_at=timezone.now() - timezone.timedelta(minutes=locked_minutes_ago),
            locked_by='dead-worker:1234',
        )
        return job

    def test_crash_point_1_after_running_before_action_execution_is_recovered(self):
        # Indistinguishable, at the DB level, from any other stale RUNNING
        # row — that's exactly the point: recovery can't know *where* the
        # worker died, only that it did.
        job = self._stuck_running_job(attempts=1, max_attempts=3)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)
        self.assertEqual(job.attempts, 1)  # not re-incremented by recovery itself
        self.assertEqual(job.locked_at, None)
        self.assertEqual(job.locked_by, '')
        self.assertIn('stale', job.last_error.lower())

    def test_crash_point_2_after_action_execution_before_succeeded_does_not_reexecute_confirmed_side_effect(self):
        """Simulates a worker that actually ran a SEND_CUSTOMER_MESSAGE
        action (message persisted) and then crashed before marking the job
        SUCCEEDED. Recovery requeues the job; the retry must not re-send the
        message — this is what the retry_seed fix (derived from the stable
        ScheduledAction.id, not a fresh per-attempt AutomationExecution.id)
        guarantees.
        """
        from conversations.models import Message
        job = self._stuck_running_job(
            action_definition={'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'hi {customer_name}'}},
            attempts=1, max_attempts=3,
        )
        # The "confirmed successful side effect" the crashed worker produced,
        # using the exact client_message_id the real handler derives from
        # ctx.retry_seed=str(job.id).
        Message.objects.create(
            conversation=self.conv, sender_type=Message.SenderType.SYSTEM, content='hi there',
            client_message_id=f'automation:{job.id}:0', message_type=Message.MessageType.TEXT,
        )
        call_command('process_automation_jobs')  # recovers to PENDING (not due yet re-claim)
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)

        job.execute_at = timezone.now() - timezone.timedelta(minutes=1)
        job.save(update_fields=['execute_at'])
        call_command('process_automation_jobs')  # now due — retried for real
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(
            Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.TEXT).count(), 1,
        )  # still exactly one — the retry recognized its own prior work and skipped re-sending

    def test_stale_lock_with_attempts_below_max_is_requeued_to_pending(self):
        job = self._stuck_running_job(attempts=1, max_attempts=3)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)
        self.assertTrue(AutomationExecution.objects.filter(
            correlation_id=job.correlation_id, status=AutomationExecution.Status.FAILED,
        ).exists())  # operator-visible audit record

    def test_stale_lock_at_max_attempts_is_permanently_failed(self):
        job = self._stuck_running_job(attempts=3, max_attempts=3)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.FAILED)
        self.assertEqual(job.attempts, 3)  # unchanged by recovery
        self.assertIn('max_attempts', job.last_error)

    def test_cancelled_job_is_never_recovered(self):
        job = self._stuck_running_job(attempts=1, max_attempts=3)
        job.status = ScheduledAction.Status.CANCELLED
        job.locked_at = timezone.now() - timezone.timedelta(minutes=30)
        job.save(update_fields=['status', 'locked_at'])
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.CANCELLED)  # recovery only ever touches RUNNING rows

    def test_workspace_a_job_cannot_execute_against_a_workspace_b_conversation(self):
        ws_b, project_b, visitor_b, conv_b = self.make_full_stack()
        # A job whose conversation legitimately belongs to workspace B but
        # whose `workspace` FK was set to workspace A (defensive check — this
        # should never happen via normal SCHEDULE_ACTION creation, which
        # always sets workspace=conv.workspace, but the execution path must
        # not blindly trust it either).
        job = ScheduledAction.objects.create(
            workspace=self.ws, conversation=conv_b, action_definition={'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
            correlation_id=uuid.uuid4(), depth=0, execute_at=timezone.now() - timezone.timedelta(minutes=1),
            status=ScheduledAction.Status.PENDING,
        )
        call_command('process_automation_jobs')
        job.refresh_from_db()
        conv_b.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)
        self.assertIn('workspace', job.last_error.lower())
        self.assertNotEqual(conv_b.priority, 'URGENT')  # the mismatched-workspace job never touched it

    def test_successful_non_stale_running_job_is_not_reclaimed(self):
        # locked_at is recent (well inside the default stale-after window) —
        # a live worker still legitimately working on it.
        job = self._stuck_running_job(attempts=1, max_attempts=3, locked_minutes_ago=1)
        call_command('process_automation_jobs', '--stale-after-seconds', '300')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.RUNNING)  # untouched
        self.assertEqual(job.locked_by, 'dead-worker:1234')

    def test_recover_stale_flag_can_be_disabled(self):
        job = self._stuck_running_job(attempts=1, max_attempts=3)
        call_command('process_automation_jobs', '--no-recover-stale')
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.RUNNING)  # recovery sweep skipped entirely


class StaleJobRecoveryRaceTests(TransactionTestCase, AutomationTestMixin):
    """TransactionTestCase (not TestCase): two real threads with their own
    DB connections are required for the row-lock contention this exercises
    to be genuine — the same reasoning as queues.tests.ConcurrentClaimTests.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def test_two_recovery_sweeps_racing_only_one_recovers_the_job(self):
        job = ScheduledAction.objects.create(
            workspace=self.ws, conversation=self.conv,
            action_definition={'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
            correlation_id=uuid.uuid4(), depth=0, execute_at=timezone.now() + timezone.timedelta(hours=1),
            status=ScheduledAction.Status.RUNNING, attempts=1, max_attempts=3,
            locked_at=timezone.now() - timezone.timedelta(minutes=10), locked_by='dead-worker:1234',
        )

        def _run():
            call_command('process_automation_jobs')
            from django.db import connection
            connection.close()

        t1 = threading.Thread(target=_run)
        t2 = threading.Thread(target=_run)
        t1.start(); t2.start()
        t1.join(); t2.join()

        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)
        # Exactly one recovery audit record — the second sweep's SELECT ...
        # FOR UPDATE SKIP LOCKED must have found nothing left to reclaim.
        self.assertEqual(
            AutomationExecution.objects.filter(correlation_id=job.correlation_id, status=AutomationExecution.Status.FAILED).count(), 1,
        )


class ScheduledLoopGuardTests(TestCase, AutomationTestMixin):
    """The scheduled-action execution path (process_automation_jobs) used to
    call execute_action() directly and enforce none of MAX_AUTOMATION_DEPTH,
    MAX_ACTIONS_PER_CORRELATION, or the same-rule re-entry guard — this
    class covers each one directly, plus an end-to-end two-rule ping-pong
    scheduled entirely through SCHEDULE_ACTION.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _due_job(self, depth=0, correlation_id=None, rule=None, action_definition=None):
        return ScheduledAction.objects.create(
            workspace=self.ws, rule=rule, conversation=self.conv,
            action_definition=action_definition or {'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}},
            correlation_id=correlation_id or uuid.uuid4(), depth=depth,
            execute_at=timezone.now() - timezone.timedelta(minutes=1), status=ScheduledAction.Status.PENDING,
        )

    def test_scheduled_action_at_allowed_depth_executes(self):
        job = self._due_job(depth=MAX_AUTOMATION_DEPTH)  # boundary — allowed
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.conv.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(self.conv.priority, 'URGENT')

    def test_scheduled_action_beyond_max_depth_is_skipped(self):
        job = self._due_job(depth=MAX_AUTOMATION_DEPTH + 1)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.conv.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)
        self.assertNotEqual(self.conv.priority, 'URGENT')  # action never ran
        skip = AutomationExecution.objects.get(correlation_id=job.correlation_id)
        self.assertEqual(skip.status, AutomationExecution.Status.SKIPPED_LOOP)
        self.assertIn('MAX_AUTOMATION_DEPTH', skip.error_summary)

    def test_scheduled_action_count_limit_is_enforced(self):
        correlation_id = uuid.uuid4()
        seed_execution = AutomationExecution.objects.create(
            workspace=self.ws, trigger_type='SEED', event_id=uuid.uuid4(), correlation_id=correlation_id,
            conversation=self.conv, status=AutomationExecution.Status.SUCCEEDED,
        )
        AutomationActionExecution.objects.bulk_create([
            AutomationActionExecution(execution=seed_execution, action_index=i, action_type='ESCALATE', status='SUCCEEDED')
            for i in range(MAX_ACTIONS_PER_CORRELATION)
        ])
        # The hard, concurrency-safe bound now lives in
        # AutomationCorrelationCounter (reserved via reserve_action_slot),
        # not a live .count() of AutomationActionExecution rows.
        AutomationCorrelationCounter.objects.create(
            correlation_id=correlation_id, workspace=self.ws, actions_reserved=MAX_ACTIONS_PER_CORRELATION,
        )
        job = self._due_job(depth=1, correlation_id=correlation_id)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.conv.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)
        self.assertNotEqual(self.conv.priority, 'URGENT')
        skip = AutomationExecution.objects.get(correlation_id=correlation_id, status=AutomationExecution.Status.SKIPPED_LOOP)
        self.assertIn('MAX_ACTIONS_PER_CORRELATION', skip.error_summary)

    def test_scheduled_action_same_rule_reentry_is_skipped(self):
        """Distinct from "the rule that scheduled this job already ran" (true
        of every scheduled job by construction) — this is "this same rule's
        scheduled follow-through already fired once in this correlation",
        e.g. a longer cycle looping back around to a rule already used.
        """
        rule = AutomationRule.objects.create(
            workspace=self.ws, name='r1', trigger_type=AutomationRule.Trigger.CONVERSATION_PRIORITY_CHANGED,
            is_active=True, conditions={}, actions=[],
        )
        correlation_id = uuid.uuid4()
        AutomationExecution.objects.create(
            workspace=self.ws, rule=rule, trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED,
            event_id=uuid.uuid4(), correlation_id=correlation_id, conversation=self.conv,
            status=AutomationExecution.Status.SUCCEEDED,
        )
        job = self._due_job(depth=1, correlation_id=correlation_id, rule=rule)
        call_command('process_automation_jobs')
        job.refresh_from_db()
        self.conv.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.SKIPPED)
        self.assertNotEqual(self.conv.priority, 'URGENT')
        skip = AutomationExecution.objects.get(correlation_id=correlation_id, status=AutomationExecution.Status.SKIPPED_LOOP)
        self.assertIn('re-entry', skip.error_summary)

    def test_two_rule_scheduled_ping_pong_terminates_with_bounded_rows_and_visible_skip(self):
        """Rule A (priority LOW -> schedule a flip to HIGH) and Rule B
        (priority HIGH -> schedule a flip to LOW) are wired entirely through
        SCHEDULE_ACTION, run by repeatedly invoking process_automation_jobs
        (as a real cron would) — the chain must settle, not grow forever,
        and the worker (the management command) must not crash.
        """
        rule_a = AutomationRule.objects.create(
            workspace=self.ws, name='ping', trigger_type=AutomationRule.Trigger.CONVERSATION_PRIORITY_CHANGED,
            is_active=True, priority=10, conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'LOW'},
            actions=[{'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 0, 'action': {'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}}}],
        )
        rule_b = AutomationRule.objects.create(
            workspace=self.ws, name='pong', trigger_type=AutomationRule.Trigger.CONVERSATION_PRIORITY_CHANGED,
            is_active=True, priority=10, conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'},
            actions=[{'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 0, 'action': {'type': 'SET_PRIORITY', 'params': {'priority': 'LOW'}}}}],
        )
        # conv starts at the model default (NORMAL, from make_full_stack) —
        # do NOT pre-set it to 'LOW' here: set_priority() below early-returns
        # (no event published at all) when the target already equals the
        # current value, so the trigger below needs a genuine change.

        # A single trigger, in its own captureOnCommitCallbacks block: two
        # sequential set_priority() calls sharing one block would both defer
        # their publish_event on_commit callbacks until the block exits —
        # by then the *second* call's write has already landed, so the
        # first call's callback would re-evaluate against already-mutated
        # state and wrongly start a second, independent chain.
        from conversations import services as conv_services
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.set_priority(self.conv, actor=None, priority='LOW', reason='kick off the chain')

        # Drain due jobs repeatedly, as a real recurring cron invocation
        # would, advancing execute_at each round (SCHEDULE_ACTION's
        # delay_minutes=0 still sets execute_at to "now" at creation time,
        # already in the past by the time the next round runs). Each
        # invocation gets its OWN captureOnCommitCallbacks wrapper: a
        # scheduled action's execution can itself publish a further nested
        # event (e.g. SET_PRIORITY -> CONVERSATION_PRIORITY_CHANGED), and
        # that on_commit callback needs to actually fire — under
        # TestCase's permanent outer transaction it otherwise never would —
        # *before* the next iteration checks what's newly due, exactly
        # mirroring how a real committing production transaction would
        # deliver it before the next cron tick runs.
        for _ in range(8):
            with self.captureOnCommitCallbacks(execute=True):
                call_command('process_automation_jobs')

        rows_after_first_drain = ScheduledAction.objects.count()
        for _ in range(8):
            with self.captureOnCommitCallbacks(execute=True):
                call_command('process_automation_jobs')  # command must not crash on an empty/terminated queue
        rows_after_second_drain = ScheduledAction.objects.count()

        self.assertEqual(rows_after_first_drain, rows_after_second_drain)  # no unbounded growth once settled
        self.assertTrue(AutomationExecution.objects.filter(
            rule__in=[rule_a, rule_b], status=AutomationExecution.Status.SKIPPED_LOOP,
        ).exists())  # the chain's termination is auditable, not silent
