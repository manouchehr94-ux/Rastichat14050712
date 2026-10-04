"""Idempotent, concurrency-safe processor for delayed automation actions
(ScheduledAction rows created by the SCHEDULE_ACTION action — spec section 6).

Run on a schedule (cron, or later a Celery beat task — deliberately
framework-agnostic so it drops into either) via:

    python manage.py process_automation_jobs

Each due job is claimed with SELECT ... FOR UPDATE SKIP LOCKED (Postgres)
inside its own short transaction and immediately flipped to RUNNING before
the lock is released, so two overlapping worker processes can never claim —
and therefore never execute — the same job twice. The actual action runs
outside that transaction (so a slow domain-service call never holds a
row lock); RUNNING already keeps every other worker out in the meantime.
A job that fails is returned to PENDING for a future retry until
`max_attempts` is reached, at which point it is permanently FAILED.

Stale-lock recovery: a worker that claims a job (marks it RUNNING) and then
crashes — killed process, host reboot, OOM — before reaching _finish()/_fail()
leaves that row permanently RUNNING with nothing to ever reclaim it, unless
something explicitly looks for RUNNING rows whose lock has clearly outlived
any real worker. That is what --stale-after-seconds/--recover-stale do below:
run automatically, every invocation, before the normal due-job pass, using
the exact same SELECT ... FOR UPDATE SKIP LOCKED claim pattern (so two
recovery sweeps racing each other can't both reclaim the same stale row) and
never re-incrementing `attempts` a second time for a lock it didn't itself
originally claim (attempts already went up once, at the original claim).
"""
import argparse
import os
import socket
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from automations.actions import ActionError, execute_action
from automations.engine import MAX_ACTIONS_PER_CORRELATION, ActionRunContext
from automations.events import MAX_AUTOMATION_DEPTH, AutomationActionContext
from automations.idempotency import reserve_action_slot
from automations.models import AutomationActionExecution, AutomationExecution, AutomationRule, ScheduledAction

DEFAULT_STALE_AFTER_SECONDS = getattr(settings, 'AUTOMATION_JOB_STALE_AFTER_SECONDS', 300)


class Command(BaseCommand):
    help = 'Execute due ScheduledAction rows (delayed automation actions).'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=200)
        parser.add_argument(
            '--stale-after-seconds', type=int, default=DEFAULT_STALE_AFTER_SECONDS,
            help='A RUNNING job whose locked_at is older than this is considered abandoned by a crashed worker.',
        )
        parser.add_argument(
            '--recover-stale', action=argparse.BooleanOptionalAction, default=True,
            help='Reclaim stale RUNNING jobs before processing due PENDING jobs (on by default; --no-recover-stale to disable).',
        )

    def handle(self, *args, **options):
        worker_id = f'{socket.gethostname()}:{os.getpid()}'
        now = timezone.now()
        supports_skip_locked = connection.features.has_select_for_update_skip_locked

        recovered_requeued = recovered_failed = 0
        if options['recover_stale']:
            recovered_requeued, recovered_failed = self._recover_stale(
                options['stale_after_seconds'], now, supports_skip_locked,
            )

        due_ids = list(
            ScheduledAction.objects.filter(
                status=ScheduledAction.Status.PENDING, execute_at__lte=now,
            ).order_by('execute_at').values_list('id', flat=True)[:options['batch_size']]
        )

        processed = succeeded = failed = skipped = 0

        for job_id in due_ids:
            job = self._claim(job_id, worker_id, now, supports_skip_locked)
            if job is None:
                continue  # already claimed by a concurrent worker, or no longer pending
            processed += 1
            outcome = self._run_job(job)
            if outcome == 'succeeded':
                succeeded += 1
            elif outcome == 'skipped':
                skipped += 1
            else:
                failed += 1

        self.stdout.write(self.style.SUCCESS(
            f'Recovered {recovered_requeued + recovered_failed} stale job(s) '
            f'({recovered_requeued} requeued, {recovered_failed} permanently failed). '
            f'Processed {processed} scheduled action(s): {succeeded} succeeded, {failed} failed, {skipped} skipped.'
        ))

    # ---------------------------------------------------------------- stale recovery

    @classmethod
    def _recover_stale(cls, stale_after_seconds, now, supports_skip_locked):
        cutoff = now - timezone.timedelta(seconds=stale_after_seconds)
        stale_ids = list(
            ScheduledAction.objects.filter(status=ScheduledAction.Status.RUNNING, locked_at__lt=cutoff)
            .order_by('locked_at').values_list('id', flat=True)
        )
        requeued = permanently_failed = 0
        for job_id in stale_ids:
            outcome = cls._recover_one(job_id, supports_skip_locked)
            if outcome == 'requeued':
                requeued += 1
            elif outcome == 'failed':
                permanently_failed += 1
            # outcome is None when a concurrent recovery sweep (or the
            # original worker, still alive after all) already claimed/
            # finished this row between the unlocked scan above and here.
        return requeued, permanently_failed

    @staticmethod
    def _recover_one(job_id, supports_skip_locked):
        """Reclaim exactly one stale RUNNING job under its own row lock.
        SELECT ... FOR UPDATE SKIP LOCKED means a second recovery sweep (or
        a normal claim) racing this one simply skips the locked row rather
        than blocking or double-recovering it — the same guarantee _claim()
        already gives the normal due-job path.
        """
        with transaction.atomic():
            qs = ScheduledAction.objects.select_for_update(skip_locked=supports_skip_locked)
            try:
                job = qs.get(pk=job_id, status=ScheduledAction.Status.RUNNING)
            except ScheduledAction.DoesNotExist:
                return None

            # attempts was already incremented once, at the original claim
            # that put this row into RUNNING — recovery documents what
            # happened and requeues/fails it, but never increments attempts
            # again for a lock it didn't itself originally take. The next
            # real claim (after requeue to PENDING) is what increments it
            # for that new attempt, exactly like any other retry.
            if job.attempts >= job.max_attempts:
                outcome = 'failed'
                new_status = ScheduledAction.Status.FAILED
                reason = (
                    f'Recovered from a stale RUNNING lock (worker {job.locked_by!r} likely crashed or was '
                    f'killed before finishing; locked_at={job.locked_at.isoformat() if job.locked_at else "?"}). '
                    f'max_attempts ({job.max_attempts}) already reached — permanently failed.'
                )
            else:
                outcome = 'requeued'
                new_status = ScheduledAction.Status.PENDING
                reason = (
                    f'Recovered from a stale RUNNING lock (worker {job.locked_by!r} likely crashed or was '
                    f'killed before finishing; locked_at={job.locked_at.isoformat() if job.locked_at else "?"}). '
                    f'Requeued for retry (attempt {job.attempts}/{job.max_attempts} preserved, not re-incremented).'
                )
            job.status = new_status
            job.last_error = reason[:2000]
            job.locked_at = None
            job.locked_by = ''
            job.save(update_fields=['status', 'last_error', 'locked_at', 'locked_by'])

            # Operator-visible audit record — separate from (and in addition
            # to) ScheduledAction.last_error, so the anomaly also shows up in
            # the execution-history API/UI, not just the scheduled-actions list.
            AutomationExecution.objects.create(
                workspace=job.workspace, rule=job.rule, rule_name_snapshot=job.rule.name if job.rule_id else '',
                trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED, event_id=uuid.uuid4(),
                correlation_id=job.correlation_id, depth=job.depth, conversation_id=job.conversation_id,
                status=AutomationExecution.Status.FAILED, condition_result=None, actor_type='SYSTEM',
                error_summary=reason[:500],
            )
        return outcome

    # ---------------------------------------------------------------- normal claim/run

    @staticmethod
    def _claim(job_id, worker_id, now, supports_skip_locked):
        with transaction.atomic():
            qs = ScheduledAction.objects.select_for_update(skip_locked=supports_skip_locked)
            try:
                job = qs.get(pk=job_id, status=ScheduledAction.Status.PENDING)
            except ScheduledAction.DoesNotExist:
                return None
            job.status = ScheduledAction.Status.RUNNING
            job.locked_at = now
            job.locked_by = worker_id
            job.attempts += 1
            job.save(update_fields=['status', 'locked_at', 'locked_by', 'attempts'])
        return job

    def _run_job(self, job):
        if job.conversation_id is None:
            self._finish(job, ScheduledAction.Status.SKIPPED, error='Conversation no longer exists.')
            return 'skipped'

        from conversations.models import Conversation
        conv = Conversation.objects.select_related(
            'workspace', 'visitor', 'assigned_to', 'team', 'queue', 'sla', 'sla__policy', 'sla__policy__calendar',
        ).filter(id=job.conversation_id).first()
        if conv is None:
            self._finish(job, ScheduledAction.Status.SKIPPED, error='Conversation no longer exists.')
            return 'skipped'
        if conv.workspace_id != job.workspace_id:
            # A conversation is never legitimately re-parented across
            # workspaces, but a scheduled job must not blindly trust
            # job.conversation_id belongs to job.workspace either — this
            # closes that gap defensively rather than assuming it.
            self._finish(job, ScheduledAction.Status.SKIPPED, error='Conversation workspace does not match the scheduled job\'s workspace.')
            return 'skipped'

        loop_reason = self._loop_guard_reason(job)
        if loop_reason:
            self._record_loop_skip(job, conv, loop_reason)
            self._finish(job, ScheduledAction.Status.SKIPPED, error=loop_reason)
            return 'skipped'

        # Hard, concurrency-safe reservation — a slot must be won BEFORE the
        # action runs. Two truly concurrent workers each processing a
        # DIFFERENT due ScheduledAction row in the SAME correlation near the
        # boundary now serialize on AutomationCorrelationCounter's row lock:
        # at most MAX_ACTIONS_PER_CORRELATION reservations can ever succeed
        # for one correlation_id, closing the race the previous unlocked
        # .count() read (both workers see "under the limit", both proceed)
        # allowed.
        if not reserve_action_slot(job.correlation_id, job.workspace_id, MAX_ACTIONS_PER_CORRELATION, reservation_key=str(job.id)):
            reason = (
                f'Correlation {job.correlation_id} reached MAX_ACTIONS_PER_CORRELATION '
                f'({MAX_ACTIONS_PER_CORRELATION}); scheduled action skipped.'
            )
            self._record_loop_skip(job, conv, reason)
            self._finish(job, ScheduledAction.Status.SKIPPED, error=reason)
            return 'skipped'

        execution = AutomationExecution.objects.create(
            workspace=job.workspace, rule=job.rule, rule_name_snapshot=job.rule.name if job.rule_id else '',
            trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED, event_id=uuid.uuid4(),
            correlation_id=job.correlation_id, depth=job.depth, conversation=conv,
            status=AutomationExecution.Status.MATCHED, condition_result=True, actor_type='SYSTEM',
        )
        action_type = job.action_definition.get('type', '')
        run_ctx = ActionRunContext(
            execution_id=execution.id, rule_id=str(job.rule_id) if job.rule_id else '',
            correlation_id=job.correlation_id, depth=job.depth, action_index=0,
            # Stable across retries of this SAME ScheduledAction (each retry
            # creates a fresh AutomationExecution, so execution_id itself
            # would mint a different client_message_id every attempt and
            # silently defeat the duplicate-send guard in actions.py).
            retry_seed=str(job.id),
        )

        try:
            with AutomationActionContext(job.correlation_id, job.depth):
                result, obj_type, obj_id = execute_action(conv, job.action_definition, run_ctx)
            AutomationActionExecution.objects.create(
                execution=execution, action_index=0, action_type=action_type,
                status=AutomationActionExecution.Status.SUCCEEDED, result_summary=result,
                affected_object_type=obj_type, affected_object_id=obj_id,
            )
            execution.status = AutomationExecution.Status.SUCCEEDED
            execution.save(update_fields=['status'])
            self._finish(job, ScheduledAction.Status.SUCCEEDED)
            return 'succeeded'
        except ActionError as exc:
            return self._fail(job, execution, action_type, str(exc)[:500])
        except Exception:
            return self._fail(job, execution, action_type, 'Internal error while running this scheduled action.')

    # ---------------------------------------------------------------- loop guards

    @staticmethod
    def _loop_guard_reason(job):
        """Mirrors engine.process_event's depth / same-rule guards for the
        scheduled-action execution path, which previously called
        execute_action() directly and enforced none of them. The
        action-count bound is enforced separately, right before execution,
        via the concurrency-safe reserve_action_slot() reservation — see
        _run_job — not here, since a plain read-then-compare check (as this
        used to do) cannot be made race-safe against a genuinely concurrent
        second worker without the row lock reserve_action_slot() takes.
        """
        if job.depth > MAX_AUTOMATION_DEPTH:
            return f'Depth {job.depth} exceeds MAX_AUTOMATION_DEPTH ({MAX_AUTOMATION_DEPTH}); scheduled action skipped.'

        if job.rule_id:
            # NOT the same check engine.process_event does for an instant
            # rule match: job.rule is the rule that SCHEDULED this action, so
            # it always already has a MATCHED/SUCCEEDED execution in this
            # correlation by construction (that's how the schedule came to
            # exist) — filtering on that would make every scheduled action
            # block itself. What this guards against instead is the same
            # rule's *deferred, scheduled* follow-through firing more than
            # once in one correlation chain (e.g. a longer cycle looping
            # back around to a rule whose scheduled action already ran).
            already_ran = AutomationExecution.objects.filter(
                rule_id=job.rule_id, correlation_id=job.correlation_id,
                trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED,
                status__in=[AutomationExecution.Status.SUCCEEDED, AutomationExecution.Status.PARTIALLY_SUCCEEDED],
            ).exists()
            if already_ran:
                return (
                    f'Rule {job.rule_id} already ran a scheduled action in correlation {job.correlation_id} '
                    f'(same-rule re-entry guard); scheduled action skipped.'
                )
        return None

    @staticmethod
    def _record_loop_skip(job, conv, reason):
        AutomationExecution.objects.create(
            workspace=job.workspace, rule=job.rule, rule_name_snapshot=job.rule.name if job.rule_id else '',
            trigger_type=AutomationRule.Trigger.SCHEDULED_TIME_REACHED, event_id=uuid.uuid4(),
            correlation_id=job.correlation_id, depth=job.depth, conversation=conv,
            status=AutomationExecution.Status.SKIPPED_LOOP, condition_result=None, actor_type='SYSTEM',
            error_summary=reason[:500],
        )

    # ---------------------------------------------------------------- outcomes

    @staticmethod
    def _fail(job, execution, action_type, error):
        AutomationActionExecution.objects.create(
            execution=execution, action_index=0, action_type=action_type,
            status=AutomationActionExecution.Status.FAILED, error_summary=error,
        )
        execution.status = AutomationExecution.Status.FAILED
        execution.error_summary = error
        execution.save(update_fields=['status', 'error_summary'])

        if job.attempts >= job.max_attempts:
            ScheduledAction.objects.filter(pk=job.pk).update(
                status=ScheduledAction.Status.FAILED, last_error=error, locked_at=None, locked_by='',
            )
        else:
            # Retryable: back to PENDING so a future run picks it up again.
            ScheduledAction.objects.filter(pk=job.pk).update(
                status=ScheduledAction.Status.PENDING, last_error=error, locked_at=None, locked_by='',
            )
        return 'failed'

    @staticmethod
    def _finish(job, status_value, error=''):
        fields = {'status': status_value, 'locked_at': None, 'locked_by': ''}
        if error:
            fields['last_error'] = error
        if status_value == ScheduledAction.Status.SUCCEEDED:
            fields['executed_at'] = timezone.now()
        ScheduledAction.objects.filter(pk=job.pk).update(**fields)
