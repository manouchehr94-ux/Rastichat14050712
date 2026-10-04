from rest_framework.test import APIClient
from django.test import TestCase

from teams.models import Team
from .models import AutomationExecution, AutomationRule, ScheduledAction
from .tests_base import AutomationTestMixin


class AutomationRuleApiTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.ws = self.make_workspace()
        self.admin = self.make_admin(self.ws)
        self.operator = self.make_operator(self.ws)

    def _rule(self, workspace=None, **kwargs):
        defaults = dict(
            workspace=workspace or self.ws, name='r', trigger_type=AutomationRule.Trigger.CONVERSATION_CREATED,
            conditions={}, actions=[],
        )
        defaults.update(kwargs)
        return AutomationRule.objects.create(**defaults)

    def test_operator_cannot_create_rule(self):
        self.login(self.client, self.operator)
        res = self.client.post('/api/v1/automations/rules/', {
            'workspace': str(self.ws.id), 'name': 'x', 'trigger_type': 'CONVERSATION_CREATED', 'conditions': {}, 'actions': [],
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_admin_can_create_rule(self):
        self.login(self.client, self.admin)
        res = self.client.post('/api/v1/automations/rules/', {
            'workspace': str(self.ws.id), 'name': 'Escalate urgent', 'trigger_type': 'CONVERSATION_CREATED',
            'conditions': {}, 'actions': [{'type': 'ESCALATE', 'params': {}}],
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(AutomationRule.objects.get(id=res.data['id']).created_by_id, self.admin.id)

    def test_create_rejects_invalid_conditions(self):
        self.login(self.client, self.admin)
        res = self.client.post('/api/v1/automations/rules/', {
            'workspace': str(self.ws.id), 'name': 'bad', 'trigger_type': 'CONVERSATION_CREATED',
            'conditions': {'field': 'not.a.real.field', 'operator': 'equals', 'value': 'x'}, 'actions': [],
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_create_rejects_cross_workspace_team_reference(self):
        other_ws = self.make_workspace()
        foreign_team = Team.objects.create(workspace=other_ws, name='Foreign')
        self.login(self.client, self.admin)
        res = self.client.post('/api/v1/automations/rules/', {
            'workspace': str(self.ws.id), 'name': 'bad ref', 'trigger_type': 'CONVERSATION_CREATED',
            'conditions': {}, 'actions': [{'type': 'ASSIGN_TO_TEAM', 'params': {'team_id': str(foreign_team.id)}}],
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_rule_list_never_leaks_workspace_where_user_is_only_operator(self):
        """The exact cross-workspace leak shape fixed by the PR #3 audit:
        being *admin* in one workspace must never surface rules from a
        *different* workspace where this user is merely an operator.
        """
        other_ws = self.make_workspace()
        from workspaces.models import WorkspaceMembership
        WorkspaceMembership.objects.create(user=self.admin, workspace=other_ws, role='WORKSPACE_OPERATOR')
        self._rule(workspace=self.ws, name='mine')
        leaked_rule = self._rule(workspace=other_ws, name='not mine')

        self.login(self.client, self.admin)
        res = self.client.get('/api/v1/automations/rules/')
        self.assertEqual(res.status_code, 200)
        returned_ids = {row['id'] for row in res.data['results']}
        self.assertNotIn(str(leaked_rule.id), returned_ids)

    def test_admin_of_other_workspace_cannot_retrieve_rule(self):
        rule = self._rule()
        other_ws = self.make_workspace()
        other_admin = self.make_admin(other_ws)
        self.login(self.client, other_admin)
        res = self.client.get(f'/api/v1/automations/rules/{rule.id}/')
        self.assertEqual(res.status_code, 404)

    def test_admin_of_other_workspace_cannot_update_rule(self):
        rule = self._rule()
        other_ws = self.make_workspace()
        other_admin = self.make_admin(other_ws)
        self.login(self.client, other_admin)
        res = self.client.patch(f'/api/v1/automations/rules/{rule.id}/', {'name': 'hijacked'}, format='json')
        self.assertEqual(res.status_code, 404)
        rule.refresh_from_db()
        self.assertEqual(rule.name, 'r')

    def test_admin_of_other_workspace_cannot_delete_rule(self):
        rule = self._rule()
        other_ws = self.make_workspace()
        other_admin = self.make_admin(other_ws)
        self.login(self.client, other_admin)
        res = self.client.delete(f'/api/v1/automations/rules/{rule.id}/')
        self.assertEqual(res.status_code, 404)
        self.assertTrue(AutomationRule.objects.filter(id=rule.id).exists())

    def test_activate_and_deactivate(self):
        rule = self._rule(is_active=False)
        self.login(self.client, self.admin)
        res = self.client.post(f'/api/v1/automations/rules/{rule.id}/activate/')
        self.assertEqual(res.status_code, 200)
        rule.refresh_from_db()
        self.assertTrue(rule.is_active)
        res = self.client.post(f'/api/v1/automations/rules/{rule.id}/deactivate/')
        rule.refresh_from_db()
        self.assertFalse(rule.is_active)

    def test_duplicate_creates_an_inactive_copy(self):
        rule = self._rule(is_active=True, actions=[{'type': 'ESCALATE', 'params': {}}])
        self.login(self.client, self.admin)
        res = self.client.post(f'/api/v1/automations/rules/{rule.id}/duplicate/')
        self.assertEqual(res.status_code, 201)
        self.assertFalse(res.data['is_active'])
        self.assertNotEqual(res.data['id'], str(rule.id))

    def test_simulate_via_api_causes_no_mutation(self):
        rule = self._rule(
            conditions={}, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}}], trigger_type='CONVERSATION_CREATED',
        )
        project = self.make_project(self.ws)
        visitor = self.make_visitor(project)
        conv = self.make_conversation(self.ws, project, visitor)
        self.login(self.client, self.admin)
        res = self.client.post(f'/api/v1/automations/rules/{rule.id}/simulate/', {'conversation_id': str(conv.id)}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['matched'])
        conv.refresh_from_db()
        self.assertNotEqual(conv.priority, 'URGENT')


class RuleValidationAndDraftSimulationApiTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.ws = self.make_workspace()
        self.admin = self.make_admin(self.ws)
        self.login(self.client, self.admin)

    def test_validate_accepts_a_good_payload(self):
        res = self.client.post('/api/v1/automations/validate/', {
            'workspace': str(self.ws.id), 'conditions': {}, 'actions': [{'type': 'ESCALATE', 'params': {}}],
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['valid'])

    def test_validate_rejects_a_bad_payload(self):
        res = self.client.post('/api/v1/automations/validate/', {
            'workspace': str(self.ws.id), 'conditions': {}, 'actions': [{'type': 'NOT_REAL', 'params': {}}],
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.data['valid'])

    def test_draft_simulation_rejects_missing_trigger_type(self):
        res = self.client.post('/api/v1/automations/simulate/', {
            'workspace': str(self.ws.id), 'conditions': {}, 'actions': [],
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_registry_returns_triggers_conditions_and_actions(self):
        res = self.client.get(f'/api/v1/automations/registry/?workspace={self.ws.id}')
        self.assertEqual(res.status_code, 200)
        self.assertIn('conversation.priority', res.data['condition_fields'])
        self.assertIn('SET_PRIORITY', res.data['actions'])
        trigger_values = {t['value'] for t in res.data['triggers']}
        self.assertIn('CONVERSATION_CREATED', trigger_values)


class ExecutionHistoryAndScheduledActionApiTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.ws = self.make_workspace()
        self.admin = self.make_admin(self.ws)
        self.project = self.make_project(self.ws)
        self.visitor = self.make_visitor(self.project)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)

    def test_execution_history_is_workspace_scoped(self):
        other_ws = self.make_workspace()
        AutomationExecution.objects.create(
            workspace=self.ws, trigger_type='CONVERSATION_CREATED', event_id='11111111-1111-1111-1111-111111111111',
            correlation_id='22222222-2222-2222-2222-222222222222', status='MATCHED',
        )
        AutomationExecution.objects.create(
            workspace=other_ws, trigger_type='CONVERSATION_CREATED', event_id='33333333-3333-3333-3333-333333333333',
            correlation_id='44444444-4444-4444-4444-444444444444', status='MATCHED',
        )
        self.login(self.client, self.admin)
        res = self.client.get(f'/api/v1/automations/execution-history/?workspace={self.ws.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 1)

    def test_operator_cannot_view_execution_history(self):
        operator = self.make_operator(self.ws)
        self.login(self.client, operator)
        res = self.client.get(f'/api/v1/automations/execution-history/?workspace={self.ws.id}')
        self.assertEqual(res.status_code, 403)

    def test_scheduled_actions_are_workspace_scoped(self):
        import uuid
        from django.utils import timezone
        other_ws = self.make_workspace()
        ScheduledAction.objects.create(
            workspace=self.ws, conversation=self.conv, action_definition={'type': 'ESCALATE', 'params': {}},
            correlation_id=uuid.uuid4(), execute_at=timezone.now(), idempotency_key='key-mine',
        )
        ScheduledAction.objects.create(
            workspace=other_ws, conversation=None, action_definition={'type': 'ESCALATE', 'params': {}},
            correlation_id=uuid.uuid4(), execute_at=timezone.now(), idempotency_key='key-other',
        )
        self.login(self.client, self.admin)
        res = self.client.get(f'/api/v1/automations/scheduled-actions/?workspace={self.ws.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 1)

    def test_cancel_scheduled_action(self):
        import uuid
        from django.utils import timezone
        scheduled = ScheduledAction.objects.create(
            workspace=self.ws, conversation=self.conv, action_definition={'type': 'ESCALATE', 'params': {}},
            correlation_id=uuid.uuid4(), execute_at=timezone.now() + timezone.timedelta(minutes=10), idempotency_key='cancel-mine',
        )
        self.login(self.client, self.admin)
        res = self.client.post(f'/api/v1/automations/scheduled-actions/{scheduled.id}/cancel/')
        self.assertEqual(res.status_code, 200)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, ScheduledAction.Status.CANCELLED)

    def test_admin_cannot_cancel_another_workspaces_scheduled_action(self):
        import uuid
        from django.utils import timezone
        other_ws = self.make_workspace()
        scheduled = ScheduledAction.objects.create(
            workspace=other_ws, conversation=None, action_definition={'type': 'ESCALATE', 'params': {}},
            correlation_id=uuid.uuid4(), execute_at=timezone.now() + timezone.timedelta(minutes=10), idempotency_key='cancel-other',
        )
        self.login(self.client, self.admin)
        res = self.client.post(f'/api/v1/automations/scheduled-actions/{scheduled.id}/cancel/')
        self.assertEqual(res.status_code, 403)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, ScheduledAction.Status.PENDING)
