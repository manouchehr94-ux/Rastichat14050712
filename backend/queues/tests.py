import threading
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import User
from accounts.presence import touch_presence
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor
from conversations.models import Conversation
from teams.models import Team, TeamMembership
from .models import Queue
from .services import auto_assign, claim_conversation, AssignmentError


class QueueWorkspaceScopeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.team = Team.objects.create(workspace=self.workspace, name='Sales')
        self.owner = User.objects.create_user(email='owner@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.owner, workspace=self.workspace, role='WORKSPACE_OWNER')
        res = self.client.post('/api/v1/auth/login/', {'email': 'owner@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_queue_creation_is_workspace_scoped(self):
        res = self.client.post('/api/v1/queues/', {
            'workspace': str(self.workspace.id), 'team': str(self.team.id), 'name': 'Sales Queue',
            'assignment_strategy': Queue.Strategy.MANUAL,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        queue = Queue.objects.get(id=res.data['id'])
        self.assertEqual(queue.workspace_id, self.workspace.id)

    def test_cannot_create_queue_with_team_from_another_workspace(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        other_team = Team.objects.create(workspace=other_ws, name='Foreign Team')
        res = self.client.post('/api/v1/queues/', {
            'workspace': str(self.workspace.id), 'team': str(other_team.id), 'name': 'Bad Queue',
        }, format='json')
        self.assertEqual(res.status_code, 404)


class RoutingStrategyTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.team = Team.objects.create(workspace=self.workspace, name='Sales')
        self.visitor = Visitor.objects.create(project=self.project)

        self.agents = []
        for i in range(3):
            user = User.objects.create_user(email=f'agent{i}@test.com', password='pass1234')
            WorkspaceMembership.objects.create(user=user, workspace=self.workspace, role='WORKSPACE_OPERATOR')
            TeamMembership.objects.create(team=self.team, user=user, is_active=True)
            touch_presence(user, explicit_status='ONLINE')
            self.agents.append(user)

    def _new_conversation(self):
        return Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )

    def test_round_robin_assignment_is_deterministic(self):
        queue = Queue.objects.create(workspace=self.workspace, team=self.team, name='RR', assignment_strategy=Queue.Strategy.ROUND_ROBIN)
        assigned = []
        for _ in range(6):
            conv = self._new_conversation()
            conv = auto_assign(conv, queue)
            assigned.append(conv.assigned_to_id)
        # Three agents, six conversations -> each agent appears exactly twice,
        # and consecutive picks never repeat the same agent back-to-back.
        self.assertEqual(sorted(str(a) for a in set(assigned)), sorted(str(a.id) for a in self.agents))
        for i in range(1, len(assigned)):
            self.assertNotEqual(assigned[i], assigned[i - 1])

    def test_least_active_assignment_picks_the_agent_with_fewest_active_conversations(self):
        queue = Queue.objects.create(workspace=self.workspace, team=self.team, name='LA', assignment_strategy=Queue.Strategy.LEAST_ACTIVE)
        # Load agent 0 and agent 1 up with existing active conversations first.
        for _ in range(3):
            Conversation.objects.create(
                visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
                status=Conversation.Status.OPEN, assigned_to=self.agents[0],
            )
        Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, assigned_to=self.agents[1],
        )
        conv = self._new_conversation()
        conv = auto_assign(conv, queue)
        self.assertEqual(conv.assigned_to_id, self.agents[2].id)

    def test_manual_strategy_never_auto_assigns(self):
        queue = Queue.objects.create(workspace=self.workspace, team=self.team, name='Manual', assignment_strategy=Queue.Strategy.MANUAL)
        conv = self._new_conversation()
        conv = auto_assign(conv, queue)
        self.assertIsNone(conv.assigned_to_id)

    def test_offline_agent_is_excluded_from_automatic_assignment(self):
        touch_presence(self.agents[0], explicit_status='OFFLINE')
        touch_presence(self.agents[1], explicit_status='OFFLINE')
        queue = Queue.objects.create(workspace=self.workspace, team=self.team, name='RR2', assignment_strategy=Queue.Strategy.ROUND_ROBIN)
        conv = self._new_conversation()
        conv = auto_assign(conv, queue)
        self.assertEqual(conv.assigned_to_id, self.agents[2].id)

    def test_inactive_user_account_cannot_be_assigned(self):
        self.agents[0].is_active = False
        self.agents[0].save(update_fields=['is_active'])
        self.agents[1].is_active = False
        self.agents[1].save(update_fields=['is_active'])
        queue = Queue.objects.create(workspace=self.workspace, team=self.team, name='RR3', assignment_strategy=Queue.Strategy.ROUND_ROBIN)
        conv = self._new_conversation()
        conv = auto_assign(conv, queue)
        self.assertEqual(conv.assigned_to_id, self.agents[2].id)

    def test_agent_at_capacity_is_skipped(self):
        for agent in self.agents[:2]:
            presence = agent.presence
            presence.max_capacity = 1
            presence.save(update_fields=['max_capacity'])
            Conversation.objects.create(
                visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
                status=Conversation.Status.OPEN, assigned_to=agent,
            )
        queue = Queue.objects.create(workspace=self.workspace, team=self.team, name='RR4', assignment_strategy=Queue.Strategy.ROUND_ROBIN)
        conv = self._new_conversation()
        conv = auto_assign(conv, queue)
        self.assertEqual(conv.assigned_to_id, self.agents[2].id)

    def test_no_eligible_agent_falls_back_to_fallback_queue(self):
        fallback_team = Team.objects.create(workspace=self.workspace, name='Fallback Team')
        fallback_agent = User.objects.create_user(email='fallback@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=fallback_agent, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=fallback_team, user=fallback_agent, is_active=True)
        touch_presence(fallback_agent, explicit_status='ONLINE')

        fallback_queue = Queue.objects.create(
            workspace=self.workspace, team=fallback_team, name='Fallback', assignment_strategy=Queue.Strategy.ROUND_ROBIN,
        )
        main_team = Team.objects.create(workspace=self.workspace, name='Empty Team')
        main_queue = Queue.objects.create(
            workspace=self.workspace, team=main_team, name='Main', assignment_strategy=Queue.Strategy.ROUND_ROBIN,
            fallback_queue=fallback_queue,
        )
        conv = self._new_conversation()
        conv = auto_assign(conv, main_queue)
        self.assertEqual(conv.assigned_to_id, fallback_agent.id)


class ConcurrentClaimTests(TransactionTestCase):
    """Uses TransactionTestCase (real, separately-committed transactions per
    thread) rather than TestCase, because the row-lock contention this test
    exercises only actually happens across independent DB connections.
    """
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.team = Team.objects.create(workspace=self.workspace, name='Sales')
        self.visitor = Visitor.objects.create(project=self.project)
        self.agent_a = User.objects.create_user(email='a@test.com', password='pass1234')
        self.agent_b = User.objects.create_user(email='b@test.com', password='pass1234')
        for agent in (self.agent_a, self.agent_b):
            WorkspaceMembership.objects.create(user=agent, workspace=self.workspace, role='WORKSPACE_OPERATOR')
            TeamMembership.objects.create(team=self.team, user=agent, is_active=True)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, team=self.team,
        )

    def test_simultaneous_claim_permits_only_one_winner(self):
        results = {}

        def _claim(agent, key):
            try:
                claim_conversation(self.conv, agent)
                results[key] = 'won'
            except AssignmentError:
                results[key] = 'lost'
            finally:
                from django.db import connection
                connection.close()

        t1 = threading.Thread(target=_claim, args=(self.agent_a, 'a'))
        t2 = threading.Thread(target=_claim, args=(self.agent_b, 'b'))
        t1.start(); t2.start()
        t1.join(); t2.join()

        outcomes = list(results.values())
        self.assertEqual(outcomes.count('won'), 1)
        self.assertEqual(outcomes.count('lost'), 1)
        self.conv.refresh_from_db()
        self.assertIn(self.conv.assigned_to_id, (self.agent_a.id, self.agent_b.id))
