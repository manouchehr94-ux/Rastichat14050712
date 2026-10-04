"""Crash-point-B regression coverage: a worker that ran an action's real
side effect and then crashed before the ScheduledAction was marked
SUCCEEDED must never re-execute that side effect on stale-job recovery.

The retry_seed mechanism previously protected only the three message
actions (SEND_CUSTOMER_MESSAGE / CREATE_INTERNAL_NOTE / REQUEST_RATING) via
client_message_id. This module proves the SAME guarantee now holds for
assignment, transfer, escalation, notification, priority, tag, and close
actions via automations.idempotency.run_idempotent (see actions.py).

Every test follows the same shape as StaleJobRecoveryTests.test_crash_point_2
in tests_scheduling.py: create a stuck RUNNING job, run execute_action()
directly ONCE to simulate the crashed worker's confirmed side effect (using
the exact retry_seed=str(job.id) the real recovered retry will also use),
then recover-and-retry via process_automation_jobs and assert the domain
state, audit/history, and notifications all still show exactly one
occurrence — not two.
"""
import uuid

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import OperatorPresence, User
from accounts.presence import touch_presence
from audit.models import AuditEvent
from conversations.models import Assignment, Conversation, Message, PriorityChange
from customer_context.models import ConversationTag, Tag
from notifications.models import Notification
from teams.models import Team, TeamMembership
from workspaces.models import WorkspaceMembership

from .actions import execute_action
from .engine import ActionRunContext
from .models import ScheduledAction
from .tests_base import AutomationTestMixin


class ActionIdempotencyCrashPointBTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    def _stuck_running_job(self, action_definition, due=False):
        execute_at = timezone.now() - timezone.timedelta(minutes=30) if due else timezone.now() + timezone.timedelta(hours=1)
        return ScheduledAction.objects.create(
            workspace=self.ws, conversation=self.conv, action_definition=action_definition,
            correlation_id=uuid.uuid4(), depth=0, execute_at=execute_at,
            status=ScheduledAction.Status.RUNNING, attempts=1, max_attempts=3,
            locked_at=timezone.now() - timezone.timedelta(minutes=10), locked_by='dead-worker:crashb',
        )

    def _simulate_confirmed_side_effect_then_recover(self, job):
        """Runs the action once directly (the crashed worker's real, already
        -committed side effect), then drives the job through recovery and a
        real retry via the actual command, exactly like a second cron tick
        would.
        """
        ctx = ActionRunContext(
            execution_id=999999, rule_id=str(job.rule_id) if job.rule_id else '',
            correlation_id=job.correlation_id, depth=0, action_index=0, retry_seed=str(job.id),
        )
        execute_action(self.conv, job.action_definition, ctx)

        call_command('process_automation_jobs')  # recovers to PENDING (not due yet)
        job.refresh_from_db()
        self.assertEqual(job.status, ScheduledAction.Status.PENDING)

        job.execute_at = timezone.now() - timezone.timedelta(minutes=1)
        job.save(update_fields=['execute_at'])
        call_command('process_automation_jobs')  # now due — the real recovered retry
        job.refresh_from_db()

    # ---------------------------------------------------------------- control

    def test_send_customer_message_control_still_protected(self):
        job = self._stuck_running_job({'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'hi there'}})
        self._simulate_confirmed_side_effect_then_recover(job)
        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(
            Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.TEXT).count(), 1,
        )

    # ---------------------------------------------------------------- assignment

    def test_assign_to_agent_is_not_duplicated(self):
        agent = User.objects.create_user(email='crashb-agent@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=agent, workspace=self.ws, role='WORKSPACE_OPERATOR')
        touch_presence(agent, explicit_status=OperatorPresence.Status.ONLINE)

        job = self._stuck_running_job({'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': str(agent.id)}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, agent.id)
        self.assertEqual(Assignment.objects.filter(conversation=self.conv).count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action='conversation_assigned', target_id=str(self.conv.id)).count(), 1,
        )
        self.assertEqual(Notification.objects.filter(recipient=agent).count(), 1)

    # ---------------------------------------------------------------- transfer

    def test_transfer_to_team_is_not_duplicated(self):
        team = Team.objects.create(workspace=self.ws, name='Destination')
        job = self._stuck_running_job({'type': 'TRANSFER_TO_TEAM', 'params': {'team_id': str(team.id)}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.team_id, team.id)
        self.assertEqual(Assignment.objects.filter(conversation=self.conv, action=Assignment.Action.TRANSFER).count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action='conversation_transferred', target_id=str(self.conv.id)).count(), 1,
        )

    # ---------------------------------------------------------------- escalation

    def test_escalate_is_not_duplicated(self):
        team = Team.objects.create(workspace=self.ws, name='Sales')
        supervisor = User.objects.create_user(email='crashb-sup@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=supervisor, workspace=self.ws, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=team, user=supervisor, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        self.conv.team = team
        self.conv.save(update_fields=['team'])

        job = self._stuck_running_job({'type': 'ESCALATE', 'params': {'reason': 'crash-b test'}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, supervisor.id)
        self.assertEqual(Assignment.objects.filter(conversation=self.conv, action=Assignment.Action.ESCALATE).count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action='conversation_escalated', target_id=str(self.conv.id)).count(), 1,
        )
        self.assertEqual(Notification.objects.filter(recipient=supervisor).count(), 1)
        # The escalation only ever elevates priority once, even across the retry.
        self.assertEqual(
            PriorityChange.objects.filter(conversation=self.conv, reason_code=PriorityChange.Reason.ESCALATION).count(), 1,
        )

    # ---------------------------------------------------------------- notifications

    def test_send_notification_is_not_duplicated(self):
        agent = User.objects.create_user(email='crashb-assignee@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=agent, workspace=self.ws, role='WORKSPACE_OPERATOR')
        self.conv.assigned_to = agent
        self.conv.save(update_fields=['assigned_to'])

        job = self._stuck_running_job({'type': 'SEND_NOTIFICATION', 'params': {'target': 'ASSIGNEE', 'title': 'Heads up'}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(Notification.objects.filter(recipient=agent).count(), 1)

    def test_notify_supervisor_is_not_duplicated(self):
        team = Team.objects.create(workspace=self.ws, name='Sales')
        supervisor = User.objects.create_user(email='crashb-notify-sup@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=supervisor, workspace=self.ws, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=team, user=supervisor, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        self.conv.team = team
        self.conv.save(update_fields=['team'])

        job = self._stuck_running_job({'type': 'NOTIFY_SUPERVISOR', 'params': {'title': 'Escalation heads up'}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(Notification.objects.filter(recipient=supervisor).count(), 1)

    def test_send_notification_partial_multi_recipient_retry_only_completes_remaining(self):
        """Two recipients; the FIRST already has a completed reservation
        (simulating "recipient A was notified before the crash, recipient B
        never got there"); the retry must notify only B, not re-notify A.
        """
        agent_a = User.objects.create_user(email='crashb-multi-a@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=agent_a, workspace=self.ws, role='WORKSPACE_OPERATOR')
        team = Team.objects.create(workspace=self.ws, name='Sales')
        TeamMembership.objects.create(team=team, user=agent_a, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        agent_b = User.objects.create_user(email='crashb-multi-b@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=agent_b, workspace=self.ws, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=team, user=agent_b, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        self.conv.team = team
        self.conv.save(update_fields=['team'])

        job = self._stuck_running_job({'type': 'NOTIFY_SUPERVISOR', 'params': {'title': 'Partial retry test'}})
        # Simulate: only agent_a was notified before the crash (not the full
        # action — a single recipient's worth of the confirmed side effect).
        # The key MUST exactly match what actions._idempotency_key() derives
        # for this recipient (ctx.retry_seed=str(job.id), rule_id='',
        # conv.id, action_type, action_index=0, extra=agent_a.id) — anything
        # else would silently defeat the dedup this test is proving.
        from .idempotency import run_idempotent
        key_a = f'{job.id}::{self.conv.id}:NOTIFY_SUPERVISOR:0:{agent_a.id}'
        from notifications.services import notify
        from notifications.models import Notification as NotifModel

        def _notify_a():
            notify(agent_a, self.ws, NotifModel.EventType.AUTOMATION_TRIGGERED, 'Partial retry test', {'conversation_id': str(self.conv.id)})
            return {'notified': str(agent_a.id)}, 'user', str(agent_a.id)

        run_idempotent(key_a, 'NOTIFY_SUPERVISOR', self.ws.id, self.conv, _notify_a)
        self.assertEqual(Notification.objects.filter(recipient=agent_a).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=agent_b).count(), 0)

        call_command('process_automation_jobs')
        job.refresh_from_db()
        job.execute_at = timezone.now() - timezone.timedelta(minutes=1)
        job.save(update_fields=['execute_at'])
        call_command('process_automation_jobs')
        job.refresh_from_db()

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(Notification.objects.filter(recipient=agent_a).count(), 1)  # still exactly one — not re-notified
        self.assertEqual(Notification.objects.filter(recipient=agent_b).count(), 1)  # the remaining recipient still got notified

    # ---------------------------------------------------------------- priority / tags

    def test_set_priority_is_not_duplicated(self):
        job = self._stuck_running_job({'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')
        self.assertEqual(PriorityChange.objects.filter(conversation=self.conv).count(), 1)

    def test_add_tag_is_not_duplicated(self):
        tag = Tag.objects.create(workspace=self.ws, name='vip')
        job = self._stuck_running_job({'type': 'ADD_TAG', 'params': {'tag_id': str(tag.id)}})
        self._simulate_confirmed_side_effect_then_recover(job)

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.assertEqual(ConversationTag.objects.filter(conversation=self.conv, tag=tag).count(), 1)

    # ---------------------------------------------------------------- close

    def test_close_conversation_is_not_duplicated(self):
        """close_conversation() has no natural no-op-if-already-closed guard
        (unlike set_priority/set_status) — every raw call re-sets closed_at
        and re-publishes CONVERSATION_CLOSED/CONVERSATION_RESOLVED with a
        FRESH event_id each time (publish_event mints a new one per call),
        so a second call is not merely redundant, it is a real duplicate
        side effect: any rule listening for CONVERSATION_CLOSED (e.g. a
        rating-request automation) would fire again. The idempotency
        wrapper must stop close_conversation() from running a second time
        at all — proven here by closed_at staying fixed at its original
        value across the recovered retry (a second real call would have
        overwritten it with a later timestamp).
        """
        job = self._stuck_running_job({'type': 'CLOSE_CONVERSATION', 'params': {}})
        ctx = ActionRunContext(
            execution_id=999999, rule_id='', correlation_id=job.correlation_id, depth=0, action_index=0,
            retry_seed=str(job.id),
        )
        execute_action(self.conv, job.action_definition, ctx)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, Conversation.Status.CLOSED)
        first_closed_at = self.conv.closed_at
        self.assertIsNotNone(first_closed_at)

        call_command('process_automation_jobs')
        job.refresh_from_db()
        job.execute_at = timezone.now() - timezone.timedelta(minutes=1)
        job.save(update_fields=['execute_at'])
        call_command('process_automation_jobs')
        job.refresh_from_db()

        self.assertEqual(job.status, ScheduledAction.Status.SUCCEEDED)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, Conversation.Status.CLOSED)
        self.assertEqual(self.conv.closed_at, first_closed_at)  # untouched by the retry — close_conversation() never ran again
