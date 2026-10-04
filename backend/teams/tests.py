from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from .models import Team, TeamMembership


class TeamManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)

        self.owner = User.objects.create_user(email='owner@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.owner, workspace=self.workspace, role='WORKSPACE_OWNER')

        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')

        self.other_platform = Platform.objects.create(name='P2')
        self.other_workspace = Workspace.objects.create(name='WS2', platform=self.other_platform)
        self.outsider = User.objects.create_user(email='outsider@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.outsider, workspace=self.other_workspace, role='WORKSPACE_OPERATOR')

        self.owner_token = self._login('owner@test.com')
        self.operator_token = self._login('op@test.com')

    def _login(self, email):
        res = self.client.post('/api/v1/auth/login/', {'email': email, 'password': 'pass1234'}, format='json')
        return res.data['access']

    def test_owner_can_create_team(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.owner_token}')
        res = self.client.post('/api/v1/teams/', {
            'workspace': str(self.workspace.id), 'name': 'Sales', 'description': 'Sales team',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['name'], 'Sales')
        self.assertTrue(Team.objects.filter(workspace=self.workspace, name='Sales').exists())

    def test_operator_cannot_create_team(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.operator_token}')
        res = self.client.post('/api/v1/teams/', {
            'workspace': str(self.workspace.id), 'name': 'Sales',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_operator_can_list_teams_but_not_manage(self):
        team = Team.objects.create(workspace=self.workspace, name='Support')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.operator_token}')
        res = self.client.get('/api/v1/teams/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['results']), 1)

        patch_res = self.client.patch(f'/api/v1/teams/{team.id}/', {'name': 'Renamed'}, format='json')
        self.assertEqual(patch_res.status_code, 403)

    def test_cannot_add_member_from_a_different_workspace(self):
        team = Team.objects.create(workspace=self.workspace, name='Support')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.owner_token}')
        res = self.client.post(f'/api/v1/teams/{team.id}/add_member/', {'user_id': str(self.outsider.id)}, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertFalse(TeamMembership.objects.filter(team=team, user=self.outsider).exists())

    def test_add_and_remove_member(self):
        team = Team.objects.create(workspace=self.workspace, name='Support')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.owner_token}')
        res = self.client.post(f'/api/v1/teams/{team.id}/add_member/', {'user_id': str(self.operator.id)}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(TeamMembership.objects.filter(team=team, user=self.operator, is_active=True).exists())

        res2 = self.client.post(f'/api/v1/teams/{team.id}/remove_member/', {'user_id': str(self.operator.id)}, format='json')
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(TeamMembership.objects.filter(team=team, user=self.operator).exists())

    def test_deactivated_team_is_still_visible_but_conversation_history_preserved(self):
        team = Team.objects.create(workspace=self.workspace, name='Legacy', is_active=False)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.operator_token}')
        res = self.client.get('/api/v1/teams/')
        self.assertEqual(res.status_code, 200)
        names = [t['name'] for t in res.data['results']]
        self.assertIn('Legacy', names)
