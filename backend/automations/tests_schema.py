from django.test import TestCase
from rest_framework.exceptions import ValidationError

from teams.models import Team
from .schema import validate_conditions, validate_actions, validate_rule_payload
from .tests_base import AutomationTestMixin


class ConditionSchemaTests(TestCase):
    def test_empty_conditions_are_valid(self):
        validate_conditions({})  # must not raise

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'conversation.does_not_exist', 'operator': 'equals', 'value': 'x'})

    def test_unknown_operator_for_field_type_rejected(self):
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'conversation.priority', 'operator': 'before', 'value': 'x'})

    def test_valueless_operator_does_not_require_value(self):
        validate_conditions({'field': 'conversation.assignee_id', 'operator': 'exists'})

    def test_operator_requiring_value_without_value_rejected(self):
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'conversation.priority', 'operator': 'equals'})

    def test_in_operator_requires_list_value(self):
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'conversation.priority', 'operator': 'in', 'value': 'HIGH'})
        validate_conditions({'field': 'conversation.priority', 'operator': 'in', 'value': ['HIGH', 'URGENT']})

    def test_condition_depth_limit_enforced(self):
        # Build a chain deeper than MAX_CONDITION_DEPTH (6) using nested "not".
        node = {'field': 'conversation.priority', 'operator': 'exists'}
        for _ in range(10):
            node = {'not': node}
        with self.assertRaises(ValidationError):
            validate_conditions(node)

    def test_condition_count_limit_enforced(self):
        many = {'all': [{'field': 'conversation.priority', 'operator': 'exists'} for _ in range(50)]}
        with self.assertRaises(ValidationError):
            validate_conditions(many)

    def test_metadata_condition_requires_alphanumeric_path(self):
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'customer.metadata', 'operator': 'equals', 'value': 'x'})
        with self.assertRaises(ValidationError):
            validate_conditions({'field': 'customer.metadata', 'operator': 'equals', 'value': 'x', 'path': 'bad path!'})
        validate_conditions({'field': 'customer.metadata', 'operator': 'equals', 'value': 'x', 'path': 'vip_level'})

    def test_leaf_without_field_or_operator_rejected(self):
        with self.assertRaises(ValidationError):
            validate_conditions({'value': 'x'})

    def test_non_dict_node_rejected(self):
        with self.assertRaises(ValidationError):
            validate_conditions('not-a-dict')


class ActionSchemaTests(TestCase, AutomationTestMixin):
    def test_unknown_action_type_rejected(self):
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'DO_MAGIC', 'params': {}}])

    def test_missing_required_param_rejected(self):
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'SET_PRIORITY', 'params': {}}])

    def test_unsupported_param_rejected(self):
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'UNASSIGN', 'params': {'unexpected': 1}}])

    def test_choices_enforced(self):
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'SET_PRIORITY', 'params': {'priority': 'NOT_A_PRIORITY'}}])
        validate_actions([{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])

    def test_max_len_enforced(self):
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'SEND_CUSTOMER_MESSAGE', 'params': {'template': 'x' * 2001}}])

    def test_workspace_scoped_reference_rejects_foreign_team(self):
        ws1 = self.make_workspace()
        ws2 = self.make_workspace()
        foreign_team = Team.objects.create(workspace=ws2, name='Foreign team')
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'ASSIGN_TO_TEAM', 'params': {'team_id': str(foreign_team.id)}}], workspace=ws1)

    def test_workspace_scoped_reference_accepts_own_team(self):
        ws1 = self.make_workspace()
        own_team = Team.objects.create(workspace=ws1, name='Own team')
        validate_actions([{'type': 'ASSIGN_TO_TEAM', 'params': {'team_id': str(own_team.id)}}], workspace=ws1)

    def test_schedule_action_disallows_nested_schedule_action(self):
        with self.assertRaises(ValidationError):
            validate_actions([{
                'type': 'SCHEDULE_ACTION',
                'params': {'delay_minutes': 5, 'action': {'type': 'SCHEDULE_ACTION', 'params': {'delay_minutes': 5, 'action': {}}}},
            }])

    def test_schedule_action_disallows_nested_cancel(self):
        with self.assertRaises(ValidationError):
            validate_actions([{
                'type': 'SCHEDULE_ACTION',
                'params': {'delay_minutes': 5, 'action': {'type': 'CANCEL_SCHEDULED_ACTION', 'params': {}}},
            }])

    def test_schedule_action_accepts_valid_inner_action(self):
        validate_actions([{
            'type': 'SCHEDULE_ACTION',
            'params': {'delay_minutes': 30, 'action': {'type': 'ESCALATE', 'params': {}}},
        }])

    def test_too_many_actions_rejected(self):
        with self.assertRaises(ValidationError):
            validate_actions([{'type': 'ESCALATE', 'params': {}} for _ in range(25)])

    def test_actions_must_be_a_list(self):
        with self.assertRaises(ValidationError):
            validate_actions({'type': 'ESCALATE'})


class RulePayloadValidationTests(TestCase):
    def test_payload_size_limit_enforced(self):
        huge_conditions = {'all': [
            {'field': 'customer.metadata', 'operator': 'equals', 'value': 'x' * 800, 'path': 'k'}
            for _ in range(30)
        ]}
        with self.assertRaises(ValidationError):
            validate_rule_payload(huge_conditions, [])

    def test_valid_payload_passes(self):
        validate_rule_payload(
            {'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'},
            [{'type': 'ESCALATE', 'params': {}}],
        )
