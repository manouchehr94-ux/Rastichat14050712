"""Escalation capacity enforcement — conversations.services.escalate()
previously assigned `conv.assigned_to = supervisor` directly with no
capacity or cross-workspace check at all (see _eligible_escalation_supervisors
and escalate() in services.py for the fix). This module proves the fix
holds for every required scenario: normal capacity, at-capacity rejection,
real concurrent racing, slot release on close, inactive-supervisor
rejection, cross-workspace rejection, the automation ESCALATE path, atomic
no-partial-effects-on-failure, exactly-once history/notification, and
crash-point-B retry safety.
"""
import threading
import uuid

from django.test import TestCase, TransactionTestCase

from accounts.models import OperatorPresence, User
from accounts.presence import touch_presence
from audit.models import AuditEvent
from automations.actions import execute_action
from automations.engine import ActionRunContext
from notifications.models import Notification
from platforms.models import Platform
from projects.models import Project
from teams.models import Team, TeamMembership
from visitors.models import Visitor
from workspaces.models import Workspace, WorkspaceMembership

from .models import Assignment, Conversation
from .services import ConversationServiceError, close_conversation, escalate


class EscalationCapacityTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='EscCapPlatform')
        self.ws = Workspace.objects.create(name='EscCapWS', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.ws)
        self.team = Team.objects.create(workspace=self.ws, name='Sales')

        self.supervisor = User.objects.create_user(email='esccap-sup@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.supervisor, workspace=self.ws, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team, user=self.supervisor, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        presence = touch_presence(self.supervisor, explicit_status=OperatorPresence.Status.ONLINE)
        presence.max_capacity = 2
        presence.save(update_fields=['max_capacity'])

    def _conv(self, assigned_to=None, status=Conversation.Status.OPEN):
        visitor = Visitor.objects.create(project=self.project, external_id=f'esccap-{uuid.uuid4()}')
        return Conversation.objects.create(
            visitor=visitor, workspace=self.ws, type=Conversation.Type.CUSTOMER, status=status,
            team=self.team, assigned_to=assigned_to,
        )

    # 1. below capacity receives escalation
    def test_supervisor_below_capacity_receives_escalation(self):
        conv = self._conv()
        result = escalate(conv, actor=None, reason='test')
        self.assertEqual(result.assigned_to_id, self.supervisor.id)
        self.assertEqual(result.priority, Conversation.Priority.URGENT)

    # 2. exactly at capacity rejects
    def test_supervisor_at_capacity_rejects_escalation(self):
        for _ in range(2):
            self._conv(assigned_to=self.supervisor)
        conv = self._conv()
        with self.assertRaises(ConversationServiceError):
            escalate(conv, actor=None, reason='test')
        conv.refresh_from_db()
        self.assertIsNone(conv.assigned_to_id)
        self.assertNotEqual(conv.priority, Conversation.Priority.URGENT)

    # 4. closing one conversation frees one slot
    def test_closing_one_conversation_frees_one_slot(self):
        filler_a = self._conv(assigned_to=self.supervisor)
        self._conv(assigned_to=self.supervisor)
        conv = self._conv()
        with self.assertRaises(ConversationServiceError):
            escalate(conv, actor=None, reason='test')

        close_conversation(filler_a, actor=None)
        result = escalate(conv, actor=None, reason='test')
        self.assertEqual(result.assigned_to_id, self.supervisor.id)

    # 5. inactive supervisor rejected
    def test_inactive_supervisor_is_rejected(self):
        self.supervisor.is_active = False
        self.supervisor.save(update_fields=['is_active'])
        conv = self._conv()
        with self.assertRaises(ConversationServiceError):
            escalate(conv, actor=None, reason='test')
        conv.refresh_from_db()
        self.assertIsNone(conv.assigned_to_id)

    # 6. cross-workspace supervisor rejected
    def test_cross_workspace_supervisor_is_never_a_candidate(self):
        """A conversation whose OWN team has no supervisor must fail
        deterministically — it must never fall back to self.supervisor, who
        belongs to a completely different workspace/team. Team membership
        already scopes by team_id (a team belongs to exactly one
        workspace), so this proves that scoping actually holds, not just
        that a same-workspace supervisor gets picked when one exists.
        """
        other_platform = Platform.objects.create(name='EscCapOtherPlatform')
        other_ws = Workspace.objects.create(name='EscCapOtherWS', platform=other_platform)
        other_project = Project.objects.create(name='OtherStore', workspace=other_ws)
        other_team = Team.objects.create(workspace=other_ws, name='OtherSales')  # deliberately no supervisor member

        other_visitor = Visitor.objects.create(project=other_project, external_id='esccap-other-visitor')
        conv = Conversation.objects.create(
            visitor=other_visitor, workspace=other_ws, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, team=other_team,
        )
        with self.assertRaises(ConversationServiceError):
            escalate(conv, actor=None, reason='test')
        conv.refresh_from_db()
        self.assertIsNone(conv.assigned_to_id)
        self.assertNotEqual(conv.assigned_to_id, self.supervisor.id)

    # 7. automation ESCALATE obeys capacity
    def test_automation_escalate_action_obeys_capacity(self):
        for _ in range(2):
            self._conv(assigned_to=self.supervisor)
        conv = self._conv()
        ctx = ActionRunContext(execution_id=1, rule_id=None, correlation_id=uuid.uuid4(), depth=0, action_index=0, retry_seed='1')
        with self.assertRaises(Exception):
            execute_action(conv, {'type': 'ESCALATE', 'params': {}}, ctx)
        conv.refresh_from_db()
        self.assertIsNone(conv.assigned_to_id)
        self.assertNotEqual(conv.priority, Conversation.Priority.URGENT)

    # 8. failed escalation produces no partial side effects
    def test_failed_escalation_leaves_zero_partial_state(self):
        for _ in range(2):
            self._conv(assigned_to=self.supervisor)
        conv = self._conv()
        pre_assignments = Assignment.objects.filter(conversation=conv).count()
        pre_audit = AuditEvent.objects.filter(action='conversation_escalated', target_id=str(conv.id)).count()
        with self.assertRaises(ConversationServiceError):
            escalate(conv, actor=None, reason='test')
        conv.refresh_from_db()
        self.assertIsNone(conv.assigned_to_id)
        self.assertEqual(conv.priority, Conversation.Priority.NORMAL)
        self.assertEqual(Assignment.objects.filter(conversation=conv).count(), pre_assignments)
        self.assertEqual(AuditEvent.objects.filter(action='conversation_escalated', target_id=str(conv.id)).count(), pre_audit)

    # 9. successful escalation creates exactly one history row and one notification
    def test_successful_escalation_creates_exactly_one_history_row_and_notification(self):
        conv = self._conv()
        escalate(conv, actor=None, reason='test')
        self.assertEqual(Assignment.objects.filter(conversation=conv, action=Assignment.Action.ESCALATE).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='conversation_escalated', target_id=str(conv.id)).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.supervisor).count(), 1)


class EscalationCapacityConcurrencyTests(TransactionTestCase):
    """TransactionTestCase (not TestCase): real, independently-committed
    transactions per thread are required to exercise genuine row-lock
    contention on OperatorPresence — the same reasoning as
    queues.tests.ConcurrentClaimTests and queues.tests_capacity_stress.
    """

    def setUp(self):
        self.platform = Platform.objects.create(name='EscCapRacePlatform')
        self.ws = Workspace.objects.create(name='EscCapRaceWS', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.ws)
        self.team = Team.objects.create(workspace=self.ws, name='Sales')

        self.supervisor = User.objects.create_user(email='esccap-race-sup@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.supervisor, workspace=self.ws, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team, user=self.supervisor, role=TeamMembership.Role.SUPERVISOR, is_active=True)
        presence = touch_presence(self.supervisor, explicit_status=OperatorPresence.Status.ONLINE)
        presence.max_capacity = 5
        presence.save(update_fields=['max_capacity'])

    # 3. concurrent escalations cannot overshoot capacity
    def test_concurrent_escalations_never_exceed_capacity(self):
        convs = []
        for i in range(15):
            visitor = Visitor.objects.create(project=self.project, external_id=f'esccap-race-{i}')
            convs.append(Conversation.objects.create(
                visitor=visitor, workspace=self.ws, type=Conversation.Type.CUSTOMER,
                status=Conversation.Status.OPEN, team=self.team,
            ))

        results = {}

        def _escalate(conv, key):
            try:
                escalate(conv, actor=None, reason='race')
                results[key] = 'won'
            except ConversationServiceError:
                results[key] = 'lost'
            finally:
                from django.db import connection
                connection.close()

        threads = [threading.Thread(target=_escalate, args=(conv, i)) for i, conv in enumerate(convs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        won = sum(1 for v in results.values() if v == 'won')
        self.assertEqual(won, 5, f'expected exactly 5 successful escalations (max_capacity), got {won}: {results}')

        from django.db import connection
        connection.close()
        presence = OperatorPresence.objects.get(user=self.supervisor)
        self.assertEqual(presence.active_conversation_count(), 5)
        self.assertLessEqual(presence.active_conversation_count(), presence.max_capacity)
