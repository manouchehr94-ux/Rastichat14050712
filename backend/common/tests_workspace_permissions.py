"""Regression tests for the cross-workspace privilege-escalation findings
from the independent audit of PR #3 (Team Operations, Queues and SLA).

Every test here uses one of two deliberately dangerous multi-workspace role
combinations — never a user with no membership at all, which the original
(vulnerable) code already handled correctly and so never exposed the bug:

- U ("admin_u"): WORKSPACE_ADMIN in Workspace A, WORKSPACE_OPERATOR
  (nothing more) in Workspace B.
- S ("supervisor_s"): SUPERVISOR of a team in Workspace A, WORKSPACE_OPERATOR
  (nothing more) in Workspace B.

Both used to be able to act on Workspace B with the authority their role in
Workspace A carried. Every test below proves that no longer holds.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor
from conversations.models import Conversation
from teams.models import Team, TeamMembership
from queues.models import Queue
from sla.models import BusinessCalendar, SLAPolicy
from collaboration.models import QuickReply

User = get_user_model()


class MultiWorkspaceFixtureMixin:
    def setUp(self):
        self.client = APIClient()

        self.platform_a = Platform.objects.create(name='Platform A')
        self.ws_a = Workspace.objects.create(name='Workspace A', platform=self.platform_a)
        self.team_a = Team.objects.create(workspace=self.ws_a, name='Team A')
        self.calendar_a = BusinessCalendar.objects.create(workspace=self.ws_a, name='Calendar A')
        self.queue_a = Queue.objects.create(workspace=self.ws_a, team=self.team_a, name='Queue A')
        self.policy_a = SLAPolicy.objects.create(
            workspace=self.ws_a, name='Policy A', first_response_target_minutes=30, resolution_target_minutes=240,
        )

        self.platform_b = Platform.objects.create(name='Platform B')
        self.ws_b = Workspace.objects.create(name='Workspace B', platform=self.platform_b)
        self.team_b = Team.objects.create(workspace=self.ws_b, name='Team B')
        self.calendar_b = BusinessCalendar.objects.create(workspace=self.ws_b, name='Calendar B')
        self.queue_b = Queue.objects.create(workspace=self.ws_b, team=self.team_b, name='Queue B')
        self.policy_b = SLAPolicy.objects.create(
            workspace=self.ws_b, name='Policy B', first_response_target_minutes=30, resolution_target_minutes=240,
        )

        self.victim_b = User.objects.create_user(email='victim-b@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.victim_b, workspace=self.ws_b, role='WORKSPACE_OPERATOR')

        # The dangerous combination: real admin power in A, nothing but
        # regular membership in B.
        self.admin_u = User.objects.create_user(email='admin-u@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.admin_u, workspace=self.ws_a, role='WORKSPACE_ADMIN')
        WorkspaceMembership.objects.create(user=self.admin_u, workspace=self.ws_b, role='WORKSPACE_OPERATOR')

        # The other dangerous combination: real supervisor power in A,
        # nothing but regular membership in B.
        self.supervisor_s = User.objects.create_user(email='supervisor-s@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.supervisor_s, workspace=self.ws_a, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team_a, user=self.supervisor_s, role='SUPERVISOR', is_active=True)
        WorkspaceMembership.objects.create(user=self.supervisor_s, workspace=self.ws_b, role='WORKSPACE_OPERATOR')

        # A genuine admin of Workspace B, for positive-control assertions.
        self.admin_b = User.objects.create_user(email='admin-b@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.admin_b, workspace=self.ws_b, role='WORKSPACE_ADMIN')

        # No relationship to Workspace B at all.
        self.stranger = User.objects.create_user(email='stranger@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.stranger, workspace=self.ws_a, role='WORKSPACE_OPERATOR')

    def login(self, user):
        res = self.client.post('/api/v1/auth/login/', {'email': user.email, 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')


class SupervisorSummaryWorkspaceScopeTests(MultiWorkspaceFixtureMixin, TestCase):
    """BLOCKING ISSUE 1 regression: IsSupervisorOrAdmin / OperationalSummaryView."""

    def setUp(self):
        super().setUp()
        proj_b = Project.objects.create(name='Store B', workspace=self.ws_b)
        visitor_b = Visitor.objects.create(project=proj_b)
        Conversation.objects.create(
            visitor=visitor_b, workspace=self.ws_b, project=proj_b, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, priority=Conversation.Priority.URGENT,
        )

    def test_supervisor_can_access_own_workspace_summary(self):
        self.login(self.supervisor_s)
        res = self.client.get(f'/api/v1/supervisor/summary/?workspace_id={self.ws_a.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['workspace_id'], str(self.ws_a.id))

    def test_supervisor_in_a_cannot_access_workspace_b_summary(self):
        self.login(self.supervisor_s)
        res = self.client.get(f'/api/v1/supervisor/summary/?workspace_id={self.ws_b.id}')
        self.assertEqual(res.status_code, 403)

    def test_admin_in_b_can_access_workspace_b_summary(self):
        self.login(self.admin_b)
        res = self.client.get(f'/api/v1/supervisor/summary/?workspace_id={self.ws_b.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['urgent_count'], 1)

    def test_regular_operator_in_b_is_denied(self):
        self.login(self.victim_b)
        res = self.client.get(f'/api/v1/supervisor/summary/?workspace_id={self.ws_b.id}')
        self.assertEqual(res.status_code, 403)

    def test_user_with_no_workspace_b_membership_is_denied(self):
        self.login(self.stranger)
        res = self.client.get(f'/api/v1/supervisor/summary/?workspace_id={self.ws_b.id}')
        self.assertEqual(res.status_code, 403)

    def test_workspace_id_alone_is_insufficient_for_admin_elsewhere(self):
        # admin_u DOES resolve into workspace B (real, if lesser, membership
        # there) — resolve_operator_workspace alone would let this through.
        # The role check must still reject it.
        self.login(self.admin_u)
        res = self.client.get(f'/api/v1/supervisor/summary/?workspace_id={self.ws_b.id}')
        self.assertEqual(res.status_code, 403)


class TeamQueueCalendarPolicyWorkspaceScopeTests(MultiWorkspaceFixtureMixin, TestCase):
    """BLOCKING ISSUE 2 regression: IsWorkspaceAdmin / WorkspaceScopedQuerysetMixin
    across TeamViewSet, QueueViewSet, BusinessCalendarViewSet, SLAPolicyViewSet.
    """

    # 1
    def test_admin_can_update_own_workspace_team(self):
        self.login(self.admin_u)
        res = self.client.patch(f'/api/v1/teams/{self.team_a.id}/', {'description': 'updated'}, format='json')
        self.assertEqual(res.status_code, 200)

    # 2
    def test_admin_in_a_cannot_update_team_b(self):
        self.login(self.admin_u)
        res = self.client.patch(f'/api/v1/teams/{self.team_b.id}/', {'description': 'hacked'}, format='json')
        self.assertIn(res.status_code, (403, 404))
        self.team_b.refresh_from_db()
        self.assertNotEqual(self.team_b.description, 'hacked')

    # 3
    def test_admin_in_a_cannot_create_team_in_b(self):
        self.login(self.admin_u)
        res = self.client.post('/api/v1/teams/', {'workspace': str(self.ws_b.id), 'name': 'Ghost Team'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Team.objects.filter(workspace=self.ws_b, name='Ghost Team').exists())

    # 4
    def test_cross_workspace_add_member_is_rejected(self):
        self.login(self.admin_u)
        res = self.client.post(
            f'/api/v1/teams/{self.team_b.id}/add_member/', {'user_id': self.victim_b.id, 'role': 'SUPERVISOR'}, format='json',
        )
        self.assertIn(res.status_code, (403, 404))
        self.assertFalse(TeamMembership.objects.filter(team=self.team_b, user=self.victim_b).exists())

    # 5
    def test_cross_workspace_remove_member_is_rejected(self):
        TeamMembership.objects.create(team=self.team_b, user=self.victim_b, role='MEMBER', is_active=True)
        self.login(self.admin_u)
        res = self.client.post(f'/api/v1/teams/{self.team_b.id}/remove_member/', {'user_id': self.victim_b.id}, format='json')
        self.assertIn(res.status_code, (403, 404))
        self.assertTrue(TeamMembership.objects.filter(team=self.team_b, user=self.victim_b).exists())

    # 6
    def test_cross_workspace_manager_assignment_is_rejected(self):
        # stranger has zero relationship to Workspace B (unlike admin_u, who
        # deliberately DOES hold a lesser membership there for the other
        # cases in this module) — a legitimate admin of B must not be able
        # to name someone with no standing in B as its team's manager.
        self.login(self.admin_b)
        res = self.client.patch(f'/api/v1/teams/{self.team_b.id}/', {'manager': self.stranger.id}, format='json')
        self.assertEqual(res.status_code, 400)
        self.team_b.refresh_from_db()
        self.assertIsNone(self.team_b.manager_id)

    # 7
    def test_cross_workspace_queue_update_is_rejected(self):
        self.login(self.admin_u)
        res = self.client.patch(f'/api/v1/queues/{self.queue_b.id}/', {'assignment_strategy': 'ROUND_ROBIN'}, format='json')
        self.assertIn(res.status_code, (403, 404))
        self.queue_b.refresh_from_db()
        self.assertEqual(self.queue_b.assignment_strategy, 'MANUAL')

    # 8
    def test_cross_workspace_queue_delete_is_rejected(self):
        self.login(self.admin_u)
        res = self.client.delete(f'/api/v1/queues/{self.queue_b.id}/')
        self.assertIn(res.status_code, (403, 404))
        self.assertTrue(Queue.objects.filter(id=self.queue_b.id).exists())

    # 9
    def test_cross_workspace_fallback_queue_is_rejected(self):
        self.login(self.admin_b)
        res = self.client.patch(f'/api/v1/queues/{self.queue_b.id}/', {'fallback_queue': str(self.queue_a.id)}, format='json')
        self.assertEqual(res.status_code, 400)
        self.queue_b.refresh_from_db()
        self.assertIsNone(self.queue_b.fallback_queue_id)

    # 10
    def test_cross_workspace_business_calendar_update_is_rejected(self):
        self.login(self.admin_u)
        res = self.client.patch(f'/api/v1/business-calendars/{self.calendar_b.id}/', {'timezone': 'Asia/Tehran'}, format='json')
        self.assertIn(res.status_code, (403, 404))
        self.calendar_b.refresh_from_db()
        self.assertEqual(self.calendar_b.timezone, 'UTC')

    # 11
    def test_cross_workspace_sla_policy_update_is_rejected(self):
        self.login(self.admin_u)
        res = self.client.patch(f'/api/v1/sla-policies/{self.policy_b.id}/', {'first_response_target_minutes': 1}, format='json')
        self.assertIn(res.status_code, (403, 404))
        self.policy_b.refresh_from_db()
        self.assertEqual(self.policy_b.first_response_target_minutes, 30)

    # 12
    def test_related_object_ids_from_another_workspace_are_rejected(self):
        self.login(self.admin_b)
        res = self.client.patch(f'/api/v1/sla-policies/{self.policy_b.id}/', {'queue': str(self.queue_a.id)}, format='json')
        self.assertEqual(res.status_code, 400)
        self.policy_b.refresh_from_db()
        self.assertIsNone(self.policy_b.queue_id)

    # 13
    def test_list_endpoints_do_not_expose_unrelated_workspace(self):
        self.login(self.stranger)
        for url in ('/api/v1/teams/', '/api/v1/queues/', '/api/v1/business-calendars/', '/api/v1/sla-policies/'):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
            rows = res.data.get('results', res.data)
            ids = [str(row['id']) for row in rows]
            self.assertNotIn(str(self.team_b.id), ids, url)
            self.assertNotIn(str(self.queue_b.id), ids, url)
            self.assertNotIn(str(self.calendar_b.id), ids, url)
            self.assertNotIn(str(self.policy_b.id), ids, url)

    # 14
    def test_direct_object_uuid_is_insufficient_without_membership(self):
        self.login(self.stranger)
        for url in (
            f'/api/v1/teams/{self.team_b.id}/', f'/api/v1/queues/{self.queue_b.id}/',
            f'/api/v1/business-calendars/{self.calendar_b.id}/', f'/api/v1/sla-policies/{self.policy_b.id}/',
        ):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 404, url)

    def test_positive_control_admin_in_b_can_update_its_own_resources(self):
        self.login(self.admin_b)
        for url, payload in (
            (f'/api/v1/teams/{self.team_b.id}/', {'description': 'legit update'}),
            (f'/api/v1/queues/{self.queue_b.id}/', {'assignment_strategy': 'ROUND_ROBIN'}),
            (f'/api/v1/business-calendars/{self.calendar_b.id}/', {'timezone': 'Asia/Tehran'}),
            (f'/api/v1/sla-policies/{self.policy_b.id}/', {'first_response_target_minutes': 5}),
        ):
            res = self.client.patch(url, payload, format='json')
            self.assertEqual(res.status_code, 200, url)


class QuickReplyWorkspaceAndScopeAuthorizationTests(MultiWorkspaceFixtureMixin, TestCase):
    """NON-BLOCKING ISSUE 1 regression: QuickReplyViewSet write authorization."""

    def setUp(self):
        super().setUp()
        self.personal_reply = QuickReply.objects.create(
            workspace=self.ws_b, scope=QuickReply.Scope.PERSONAL, owner=self.victim_b, title='Mine', body='hi',
        )
        self.workspace_reply = QuickReply.objects.create(
            workspace=self.ws_b, scope=QuickReply.Scope.WORKSPACE, title='Official', body='hello',
        )
        self.team_reply = QuickReply.objects.create(
            workspace=self.ws_b, scope=QuickReply.Scope.TEAM, team=self.team_b, title='Team reply', body='hey',
        )
        TeamMembership.objects.create(team=self.team_b, user=self.victim_b, role='MEMBER', is_active=True)

    def test_regular_operator_cannot_update_workspace_quick_reply(self):
        self.login(self.victim_b)
        res = self.client.patch(f'/api/v1/quick-replies/{self.workspace_reply.id}/', {'body': 'hacked'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_regular_operator_cannot_delete_workspace_quick_reply(self):
        self.login(self.victim_b)
        res = self.client.delete(f'/api/v1/quick-replies/{self.workspace_reply.id}/')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(QuickReply.objects.filter(id=self.workspace_reply.id).exists())

    def test_admin_can_update_workspace_quick_reply(self):
        self.login(self.admin_b)
        res = self.client.patch(f'/api/v1/quick-replies/{self.workspace_reply.id}/', {'body': 'official update'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_admin_can_delete_workspace_quick_reply(self):
        self.login(self.admin_b)
        res = self.client.delete(f'/api/v1/quick-replies/{self.workspace_reply.id}/')
        self.assertEqual(res.status_code, 204)

    def test_personal_owner_can_manage_own_reply(self):
        self.login(self.victim_b)
        res = self.client.patch(f'/api/v1/quick-replies/{self.personal_reply.id}/', {'body': 'updated mine'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_another_operator_cannot_modify_someone_elses_personal_reply(self):
        other = User.objects.create_user(email='other-in-b@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=other, workspace=self.ws_b, role='WORKSPACE_OPERATOR')
        self.login(other)
        res = self.client.patch(f'/api/v1/quick-replies/{self.personal_reply.id}/', {'body': 'hacked'}, format='json')
        # Not even visible to `other` via get_queryset (PERSONAL, not theirs).
        self.assertEqual(res.status_code, 404)

    def test_team_member_without_supervisor_role_cannot_mutate_team_reply(self):
        self.login(self.victim_b)  # active MEMBER of team_b, not SUPERVISOR
        res = self.client.patch(f'/api/v1/quick-replies/{self.team_reply.id}/', {'body': 'hacked'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_cross_team_quick_reply_mutation_rejected(self):
        outsider = User.objects.create_user(email='team-a-member@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=outsider, workspace=self.ws_b, role='WORKSPACE_OPERATOR')
        TeamMembership.objects.create(team=self.team_a, user=outsider, role='SUPERVISOR', is_active=True)
        self.login(outsider)
        res = self.client.patch(f'/api/v1/quick-replies/{self.team_reply.id}/', {'body': 'hacked'}, format='json')
        # outsider is a WORKSPACE_OPERATOR of B but not a member of team_b at
        # all, so team_reply isn't even in their queryset.
        self.assertEqual(res.status_code, 404)

    def test_team_supervisor_can_mutate_team_reply(self):
        TeamMembership.objects.filter(team=self.team_b, user=self.victim_b).update(role='SUPERVISOR')
        self.login(self.victim_b)
        res = self.client.patch(f'/api/v1/quick-replies/{self.team_reply.id}/', {'body': 'approved update'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_scope_cannot_be_changed_via_update(self):
        self.login(self.victim_b)
        res = self.client.patch(
            f'/api/v1/quick-replies/{self.personal_reply.id}/', {'scope': QuickReply.Scope.WORKSPACE}, format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.personal_reply.refresh_from_db()
        self.assertEqual(self.personal_reply.scope, QuickReply.Scope.PERSONAL)
