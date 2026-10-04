from rest_framework.test import APITestCase

from .models import Macro
from .tests_base import MacroTestMixin

BASIC_ACTIONS = [{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'یادداشت'}}]


class MacroPermissionTests(MacroTestMixin, APITestCase):
    def test_admin_creates_macro(self):
        ws = self.make_workspace()
        admin = self.make_admin(ws)
        self.login(self.client, admin)
        res = self.client.post('/api/v1/macros/', {
            'workspace': ws.id, 'name': 'درخواست مرجوعی', 'visibility': Macro.Visibility.WORKSPACE,
            'actions': BASIC_ACTIONS,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(Macro.objects.filter(workspace=ws).count(), 1)

    def test_operator_cannot_create_workspace_macro(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        self.login(self.client, operator)
        res = self.client.post('/api/v1/macros/', {
            'workspace': ws.id, 'name': 'ماکرو تیمی', 'visibility': Macro.Visibility.WORKSPACE, 'actions': BASIC_ACTIONS,
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_operator_can_create_private_macro(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        self.login(self.client, operator)
        res = self.client.post('/api/v1/macros/', {
            'workspace': ws.id, 'name': 'ماکرو شخصی', 'visibility': Macro.Visibility.PRIVATE, 'actions': BASIC_ACTIONS,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data['owner'], operator.id)

    def test_operator_cannot_edit_others_private_macro(self):
        ws = self.make_workspace()
        owner = self.make_operator(ws)
        other = self.make_operator(ws)
        macro = Macro.objects.create(
            workspace=ws, name='خصوصی', visibility=Macro.Visibility.PRIVATE, owner=owner, actions=BASIC_ACTIONS,
        )
        self.login(self.client, other)
        res = self.client.patch(f'/api/v1/macros/{macro.id}/', {'name': 'تغییر یافته'}, format='json')
        # A stranger to this PRIVATE macro can't even see it exists — same
        # "never leak existence of unauthorized content" answer the public
        # Knowledge Base endpoints give for draft/internal articles.
        self.assertEqual(res.status_code, 404)
        macro.refresh_from_db()
        self.assertEqual(macro.name, 'خصوصی')

    def test_operator_cannot_self_promote_private_macro_to_workspace(self):
        ws = self.make_workspace()
        owner = self.make_operator(ws)
        macro = Macro.objects.create(
            workspace=ws, name='خصوصی', visibility=Macro.Visibility.PRIVATE, owner=owner, actions=BASIC_ACTIONS,
        )
        self.login(self.client, owner)
        res = self.client.patch(f'/api/v1/macros/{macro.id}/', {'visibility': Macro.Visibility.WORKSPACE}, format='json')
        self.assertEqual(res.status_code, 200, res.content)
        macro.refresh_from_db()
        # The visibility change is silently ignored for a non-admin — never
        # applied, never a hidden escalation.
        self.assertEqual(macro.visibility, Macro.Visibility.PRIVATE)

    def test_cross_workspace_resource_rejected_on_create(self):
        ws1 = self.make_workspace()
        ws2 = self.make_workspace()
        admin1 = self.make_admin(ws1)
        team2 = self.make_team(ws2)
        self.login(self.client, admin1)
        res = self.client.post('/api/v1/macros/', {
            'workspace': ws1.id, 'name': 'انتقال نامعتبر', 'visibility': Macro.Visibility.WORKSPACE,
            'actions': [{'type': 'TRANSFER_TO_TEAM', 'params': {'team_id': str(team2.id)}}],
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_private_visibility_only_owner_can_view(self):
        ws = self.make_workspace()
        owner = self.make_operator(ws)
        other = self.make_operator(ws)
        macro = Macro.objects.create(
            workspace=ws, name='خصوصی', visibility=Macro.Visibility.PRIVATE, owner=owner, is_active=True, actions=BASIC_ACTIONS,
        )
        self.login(self.client, other)
        res = self.client.get(f'/api/v1/macros/{macro.id}/')
        self.assertEqual(res.status_code, 404)
        self.login(self.client, owner)
        res = self.client.get(f'/api/v1/macros/{macro.id}/')
        self.assertEqual(res.status_code, 200)

    def test_team_visibility_only_team_members_can_view(self):
        ws = self.make_workspace()
        team = self.make_team(ws)
        member = self.make_operator(ws)
        self.add_team_member(team, member)
        outsider = self.make_operator(ws)
        macro = Macro.objects.create(
            workspace=ws, name='ماکرو تیمی', visibility=Macro.Visibility.TEAM, team=team, is_active=True, actions=BASIC_ACTIONS,
        )
        self.login(self.client, outsider)
        res = self.client.get(f'/api/v1/macros/{macro.id}/')
        self.assertEqual(res.status_code, 404)
        self.login(self.client, member)
        res = self.client.get(f'/api/v1/macros/{macro.id}/')
        self.assertEqual(res.status_code, 200)

    def test_workspace_visibility_any_operator_can_view(self):
        ws = self.make_workspace()
        admin = self.make_admin(ws)
        operator = self.make_operator(ws)
        macro = Macro.objects.create(
            workspace=ws, name='همگانی', visibility=Macro.Visibility.WORKSPACE, is_active=True, actions=BASIC_ACTIONS,
        )
        self.login(self.client, operator)
        res = self.client.get(f'/api/v1/macros/{macro.id}/')
        self.assertEqual(res.status_code, 200)

    def test_mixed_role_cross_workspace_user_denied(self):
        """A user who is Admin in workspace A and only an Operator in
        workspace B must never manage workspace B's macros using workspace
        A's admin privileges.
        """
        ws_a = self.make_workspace()
        ws_b = self.make_workspace()
        user = self.make_user()
        from workspaces.models import WorkspaceMembership
        WorkspaceMembership.objects.create(user=user, workspace=ws_a, role='WORKSPACE_OWNER')
        WorkspaceMembership.objects.create(user=user, workspace=ws_b, role='WORKSPACE_OPERATOR')

        self.login(self.client, user)
        res = self.client.post('/api/v1/macros/', {
            'workspace': ws_b.id, 'name': 'تلاش برای مدیریت', 'visibility': Macro.Visibility.WORKSPACE,
            'actions': BASIC_ACTIONS,
        }, format='json')
        self.assertEqual(res.status_code, 403)
