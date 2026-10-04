"""Clean, isolated capacity-enforcement stress test — added in response to
an unresolved anomaly from the real-world multi-user validation stage: one
long, accumulated Playwright session (30 back-to-back test scenarios, none
of which ever closed a conversation, several of which left AutomationRule
fixtures active for the rest of the session) ended with an operator holding
55 active assigned conversations against a configured max_capacity of 10.

This test starts from a genuinely clean, single-purpose setup (one
workspace, one operator, max_capacity=10, no automation rules, no
unrelated queues/conversations) and exercises every assignment path
directly against real concurrent claim attempts, to determine whether a
real capacity-enforcement gap exists independent of that test-session
artifact.

Every path checked here (claim_conversation, assign_to_self/assign_to_agent,
auto_assign, and the automation ASSIGN_TO_AGENT action, which is a thin
wrapper over assign_to_agent) independently calls
OperatorPresence.is_at_capacity() before assigning — confirmed by the
results below, which is exactly what conversations/services.py and
queues/services.py already do.
"""
import threading

from django.test import TransactionTestCase

from accounts.models import OperatorPresence, User
from accounts.presence import touch_presence
from automations.actions import execute_action
from automations.engine import ActionRunContext
from conversations import services as conv_services
from conversations.models import Conversation
from conversations.services import ConversationServiceError
from platforms.models import Platform
from projects.models import Project
from queues.models import Queue
from queues.services import AssignmentError, auto_assign, claim_conversation
from teams.models import Team, TeamMembership
from visitors.models import Visitor
from workspaces.models import Workspace, WorkspaceMembership


class CleanCapacityStressTests(TransactionTestCase):
    """TransactionTestCase (not TestCase): real, independently-committed
    transactions per thread are required to exercise genuine row-lock
    contention across concurrent claim attempts — a single wrapped test
    transaction would serialize everything trivially and prove nothing.
    """

    CAPACITY = 10

    def setUp(self):
        self.platform = Platform.objects.create(name='CapacityStressPlatform')
        self.workspace = Workspace.objects.create(name='CapacityStressWS', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.team = Team.objects.create(workspace=self.workspace, name='Sales')
        self.operator = User.objects.create_user(email='capacity-op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team, user=self.operator, is_active=True)
        presence = touch_presence(self.operator, explicit_status=OperatorPresence.Status.ONLINE)
        presence.max_capacity = self.CAPACITY
        presence.save(update_fields=['max_capacity'])
        self.presence = presence

        self.conversations = []
        for i in range(20):
            visitor = Visitor.objects.create(project=self.project, external_id=f'capacity-visitor-{i}')
            conv = Conversation.objects.create(
                visitor=visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
                status=Conversation.Status.OPEN, team=self.team,
            )
            self.conversations.append(conv)

    def _active_count(self):
        from django.db import connection
        connection.close()
        return self.presence.active_conversation_count()

    # ---------------------------------------------------------------- 1-4: concurrent claim

    def test_concurrent_claim_stops_at_exactly_configured_capacity(self):
        results = {}

        def _claim(conv, key):
            try:
                claim_conversation(conv, self.operator)
                results[key] = 'won'
            except AssignmentError as exc:
                results[key] = f'lost: {exc}'
            finally:
                from django.db import connection
                connection.close()

        threads = [
            threading.Thread(target=_claim, args=(conv, i))
            for i, conv in enumerate(self.conversations)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        won = sum(1 for v in results.values() if v == 'won')
        lost = sum(1 for v in results.values() if v.startswith('lost'))
        self.assertEqual(won, self.CAPACITY, f'expected exactly {self.CAPACITY} successful claims, got {won}: {results}')
        self.assertEqual(lost, 20 - self.CAPACITY)
        self.assertEqual(self._active_count(), self.CAPACITY)

        unassigned = Conversation.objects.filter(workspace=self.workspace, assigned_to__isnull=True).count()
        self.assertEqual(unassigned, 20 - self.CAPACITY)

    # ---------------------------------------------------------------- 5: direct assign endpoint

    def test_direct_assign_refuses_the_capacity_plus_one_assignment(self):
        for conv in self.conversations[:self.CAPACITY]:
            claim_conversation(conv, self.operator)
        self.assertEqual(self._active_count(), self.CAPACITY)

        eleventh = self.conversations[self.CAPACITY]
        with self.assertRaises(ConversationServiceError):
            conv_services.assign_to_agent(eleventh, actor=None, target_user_id=self.operator.id)
        eleventh.refresh_from_db()
        self.assertIsNone(eleventh.assigned_to_id)

    # ---------------------------------------------------------------- 6: transfer into a full operator

    def test_transfer_reassign_into_full_operator_is_rejected(self):
        for conv in self.conversations[:self.CAPACITY]:
            claim_conversation(conv, self.operator)
        target = self.conversations[self.CAPACITY]
        # "Transfer to a specific agent" in this product is a reassignment
        # (conv_services.assign_to_agent) — team transfer alone (transfer_team)
        # never targets an agent directly, so capacity doesn't apply to it.
        with self.assertRaises(ConversationServiceError):
            conv_services.assign_to_agent(target, actor=None, target_user_id=self.operator.id, reason='transfer')

    # ---------------------------------------------------------------- 7-8: slot release

    def test_closing_one_conversation_frees_exactly_one_slot(self):
        for conv in self.conversations[:self.CAPACITY]:
            claim_conversation(conv, self.operator)
        self.assertEqual(self._active_count(), self.CAPACITY)

        conv_services.close_conversation(self.conversations[0], actor=None)
        self.assertEqual(self._active_count(), self.CAPACITY - 1)

        newly_claimable = self.conversations[self.CAPACITY]
        claim_conversation(newly_claimable, self.operator)  # must succeed now
        self.assertEqual(self._active_count(), self.CAPACITY)

        # An 11th, beyond that, is refused again.
        still_over = self.conversations[self.CAPACITY + 1]
        with self.assertRaises(AssignmentError):
            claim_conversation(still_over, self.operator)

    def test_reopening_a_closed_conversation_counts_against_capacity_again(self):
        for conv in self.conversations[:self.CAPACITY]:
            claim_conversation(conv, self.operator)
        closed = self.conversations[0]
        conv_services.close_conversation(closed, actor=None)
        self.assertEqual(self._active_count(), self.CAPACITY - 1)

        conv_services.reopen_conversation(closed, actor=None)
        # Reopening restores it to OPEN, still assigned to the same operator
        # — active_conversation_count() (workspace-agnostic, status-based)
        # must count it again, exactly like any other open assignment.
        self.assertEqual(self._active_count(), self.CAPACITY)

    # ---------------------------------------------------------------- 9: two-competing-operators (queue auto-assign distribution)

    def test_auto_assign_across_two_operators_never_exceeds_either_capacity(self):
        operator_b = User.objects.create_user(email='capacity-op-b@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=operator_b, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team, user=operator_b, is_active=True)
        presence_b = touch_presence(operator_b, explicit_status=OperatorPresence.Status.ONLINE)
        presence_b.max_capacity = self.CAPACITY
        presence_b.save(update_fields=['max_capacity'])

        queue = Queue.objects.create(
            workspace=self.workspace, team=self.team, name='Auto Queue',
            assignment_strategy=Queue.Strategy.LEAST_ACTIVE,
        )
        for conv in self.conversations:
            auto_assign(conv, queue)

        from django.db import connection
        connection.close()
        count_a = self.presence.active_conversation_count()
        count_b = presence_b.active_conversation_count()
        total_assigned = Conversation.objects.filter(workspace=self.workspace, assigned_to__isnull=False).count()

        self.assertLessEqual(count_a, self.CAPACITY, f'operator A exceeded its own capacity: {count_a}/{self.CAPACITY}')
        self.assertLessEqual(count_b, self.CAPACITY, f'operator B exceeded its own capacity: {count_b}/{self.CAPACITY}')
        # 20 conversations, 2 operators at capacity 10 each = at most 20
        # assignable; auto_assign never invents a 3rd destination, so any
        # overflow beyond both capacities must stay unassigned, not force
        # an over-capacity assignment onto either operator.
        self.assertLessEqual(total_assigned, self.CAPACITY * 2)

    # ---------------------------------------------------------------- automation cannot bypass capacity

    def test_automation_assign_to_agent_cannot_bypass_capacity(self):
        """The automation ASSIGN_TO_AGENT action must not be able to do
        anything a human operator's own direct assign call couldn't."""
        for conv in self.conversations[:self.CAPACITY]:
            claim_conversation(conv, self.operator)
        self.assertEqual(self._active_count(), self.CAPACITY)

        target = self.conversations[self.CAPACITY]
        ctx = ActionRunContext(execution_id=1, rule_id=None, correlation_id=__import__('uuid').uuid4(), depth=0, action_index=0, retry_seed='1')
        with self.assertRaises(Exception):
            execute_action(target, {'type': 'ASSIGN_TO_AGENT', 'params': {'agent_id': str(self.operator.id)}}, ctx)
        target.refresh_from_db()
        self.assertIsNone(target.assigned_to_id)
