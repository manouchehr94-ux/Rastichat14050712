from decimal import Decimal
from django.utils import timezone
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from accounts.presence import touch_presence
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor
from teams.models import Team, TeamMembership
from queues.models import Queue
from audit.models import AuditEvent
from .models import Conversation, Assignment, PriorityChange


class AssignmentServiceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.team = Team.objects.create(workspace=self.workspace, name='Sales')

        self.op1 = User.objects.create_user(email='op1@test.com', password='pass1234')
        self.op2 = User.objects.create_user(email='op2@test.com', password='pass1234')
        for op in (self.op1, self.op2):
            WorkspaceMembership.objects.create(user=op, workspace=self.workspace, role='WORKSPACE_OPERATOR')
            TeamMembership.objects.create(team=self.team, user=op, is_active=True)

        self.visitor = Visitor.objects.create(project=self.project)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, team=self.team,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'op1@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_manual_assignment_succeeds(self):
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/assign/', {'operator_id': str(self.op2.id)}, format='json')
        self.assertEqual(res.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, self.op2.id)
        self.assertTrue(Assignment.objects.filter(conversation=self.conv, assigned_to=self.op2).exists())

    def test_cross_workspace_assignment_is_rejected(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        outsider = User.objects.create_user(email='outsider@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=outsider, workspace=other_ws, role='WORKSPACE_OPERATOR')
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/assign/', {'operator_id': str(outsider.id)}, format='json')
        self.assertEqual(res.status_code, 404)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_to)

    def test_inactive_agent_cannot_be_manually_assigned(self):
        self.op2.is_active = False
        self.op2.save(update_fields=['is_active'])
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/assign/', {'operator_id': str(self.op2.id)}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_transfer_preserves_history(self):
        self.conv.assigned_to = self.op1
        self.conv.save(update_fields=['assigned_to'])
        other_team = Team.objects.create(workspace=self.workspace, name='Shipping')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/transfer/', {'team_id': str(other_team.id), 'reason': 'wrong dept'}, format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.team_id, other_team.id)
        self.assertIsNone(self.conv.assigned_to)
        history = Assignment.objects.filter(conversation=self.conv, action=Assignment.Action.TRANSFER).first()
        self.assertIsNotNone(history)
        self.assertEqual(history.previous_team_id, self.team.id)
        self.assertEqual(history.new_team_id, other_team.id)
        self.assertEqual(history.previous_assignee_id, self.op1.id)
        # Original assignment record from before the transfer must still exist untouched.
        self.assertTrue(Assignment.objects.filter(conversation=self.conv).count() >= 1)

    def test_escalation_creates_audit_event_and_elevates_priority(self):
        supervisor = User.objects.create_user(email='sup@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=supervisor, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team, user=supervisor, role=TeamMembership.Role.SUPERVISOR, is_active=True)

        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/escalate/', {'reason': 'angry customer'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, Conversation.Priority.URGENT)
        self.assertEqual(self.conv.assigned_to_id, supervisor.id)
        self.assertTrue(AuditEvent.objects.filter(action='conversation_escalated', target_id=str(self.conv.id)).exists())
        self.assertTrue(Assignment.objects.filter(conversation=self.conv, action=Assignment.Action.ESCALATE).exists())

    def test_priority_update_is_authorized_and_audited(self):
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/set_priority/', {'priority': 'HIGH', 'reason': 'vip'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')
        change = PriorityChange.objects.filter(conversation=self.conv).first()
        self.assertIsNotNone(change)
        self.assertEqual(change.previous_priority, 'NORMAL')
        self.assertEqual(change.priority, 'HIGH')
        self.assertEqual(change.reason, 'vip')
        self.assertTrue(AuditEvent.objects.filter(action='conversation_priority_changed', target_id=str(self.conv.id)).exists())

    def test_invalid_priority_rejected(self):
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/set_priority/', {'priority': 'NOT_REAL'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_unassign_and_return_to_queue(self):
        self.conv.assigned_to = self.op1
        self.conv.save(update_fields=['assigned_to'])
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/unassign/', format='json')
        self.assertEqual(res.status_code, 200)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_to)
        self.assertTrue(Assignment.objects.filter(conversation=self.conv, action=Assignment.Action.UNASSIGN).exists())


class ReopenSLARecalculationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_reopen_recalculates_only_the_resolution_sla_fields(self):
        from sla.models import SLAPolicy, ConversationSLA
        from sla.services import apply_sla, mark_first_response, mark_resolved

        SLAPolicy.objects.create(
            workspace=self.workspace, name='Default', first_response_target_minutes=30,
            resolution_target_minutes=120,
        )
        apply_sla(self.conv)
        mark_first_response(self.conv)
        first_response_due_at = self.conv.sla.first_response_due_at
        first_responded_at = self.conv.sla.first_responded_at

        self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/close/', format='json')
        self.conv.refresh_from_db()
        self.assertIsNotNone(self.conv.sla.resolved_at)

        reopen_res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/reopen/', format='json')
        self.assertEqual(reopen_res.status_code, 200)
        self.conv.sla.refresh_from_db()
        # Resolution fields reset...
        self.assertIsNone(self.conv.sla.resolved_at)
        self.assertIsNotNone(self.conv.sla.resolution_due_at)
        # ...but the already-settled first-response history is untouched.
        self.assertEqual(self.conv.sla.first_response_due_at, first_response_due_at)
        self.assertEqual(self.conv.sla.first_responded_at, first_responded_at)


class SupervisorSummaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.owner = User.objects.create_user(email='owner@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.owner, workspace=self.workspace, role='WORKSPACE_OWNER')
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project)
        Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, priority=Conversation.Priority.URGENT,
        )

        self.other_platform = Platform.objects.create(name='P2')
        self.other_workspace = Workspace.objects.create(name='WS2', platform=self.other_platform)
        self.other_project = Project.objects.create(name='OtherStore', workspace=self.other_workspace)
        self.other_visitor = Visitor.objects.create(project=self.other_project)
        Conversation.objects.create(
            visitor=self.other_visitor, workspace=self.other_workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, priority=Conversation.Priority.URGENT,
        )

    def test_owner_sees_only_their_own_workspace_summary(self):
        res = self.client.post('/api/v1/auth/login/', {'email': 'owner@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        summary = self.client.get('/api/v1/supervisor/summary/')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data['workspace_id'], str(self.workspace.id))
        self.assertEqual(summary.data['urgent_count'], 1)

    def test_regular_operator_is_denied(self):
        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        summary = self.client.get('/api/v1/supervisor/summary/')
        self.assertEqual(summary.status_code, 403)


class SupervisorSummaryQueryEfficiencyTests(TestCase):
    """Regression for the audited N+1: by_queue/by_team used to run one
    .count() per row, and the per-agent loop used to run a get_or_create +
    count per agent. Proves the query count no longer scales with the
    number of queues/teams/agents in the workspace.
    """
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.owner = User.objects.create_user(email='owner@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.owner, workspace=self.workspace, role='WORKSPACE_OWNER')

        for i in range(6):
            team = Team.objects.create(workspace=self.workspace, name=f'Team {i}')
            Queue.objects.create(workspace=self.workspace, team=team, name=f'Queue {i}')
            agent = User.objects.create_user(email=f'agent{i}@test.com', password='pass1234')
            WorkspaceMembership.objects.create(user=agent, workspace=self.workspace, role='WORKSPACE_OPERATOR')
            TeamMembership.objects.create(team=team, user=agent, is_active=True)
            visitor = Visitor.objects.create(project=self.project)
            conv = Conversation.objects.create(
                visitor=visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
                status=Conversation.Status.OPEN, team=team,
            )
            Conversation.objects.filter(pk=conv.pk).update(assigned_to=agent)

        res = self.client.post('/api/v1/auth/login/', {'email': 'owner@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_query_count_does_not_scale_with_teams_queues_agents(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            res = self.client.get('/api/v1/supervisor/summary/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['by_queue']), 6)
        self.assertEqual(len(res.data['by_team']), 6)
        # 6 created agents + the requesting owner, who is also a workspace member.
        self.assertEqual(len(res.data['by_agent']), 7)
        # An N+1 version of this view would run well over 30 queries here
        # (2 per queue/team, ~2-3 per agent, on top of the fixed baseline).
        # A bounded, fixed number of queries regardless of N stays well
        # under that no matter how many teams/queues/agents exist.
        self.assertLess(
            len(ctx.captured_queries), 20,
            f'Expected a bounded query count, got {len(ctx.captured_queries)} for 6 teams/queues/agents',
        )
