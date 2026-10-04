"""Strict, declarative schema validation for AutomationRule.conditions and
.actions. This is the whole ballgame for "no dynamic code execution" and
"no arbitrary ORM field traversal": every field/operator/action name must
be an exact match against a fixed, hardcoded registry below. There is no
getattr()-by-string-from-request-data anywhere in this module or in
automations/conditions.py / automations/actions.py that consume it — field
access is always a hardcoded Python function keyed by the validated name.

Condition tree grammar (JSON):

    {"all": [<condition>, ...]}
    {"any": [<condition>, ...]}
    {"not": <condition>}
    {"field": "<name>", "operator": "<op>", "value": <any>}

A bare leaf condition (no "all"/"any"/"not" key) is treated as a single
implicit ALL group of one. An empty {} matches everything (no conditions
configured yet — used by brand-new/simulated rules).

Action list grammar (JSON): a list of
    {"type": "<ACTION_TYPE>", "params": {...}}
"""
import json

from rest_framework.exceptions import ValidationError

SCHEMA_VERSION = 1

MAX_CONDITION_DEPTH = 6
MAX_CONDITIONS_TOTAL = 40
MAX_ACTIONS_PER_RULE = 20
MAX_PAYLOAD_BYTES = 20_000
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 80

STRING_OPS = {'equals', 'not_equals', 'in', 'not_in', 'contains', 'not_contains', 'starts_with', 'ends_with', 'exists', 'not_exists'}
NUMBER_OPS = {'equals', 'not_equals', 'greater_than', 'greater_than_or_equal', 'less_than', 'less_than_or_equal', 'in', 'not_in', 'exists', 'not_exists'}
DATETIME_OPS = {'before', 'after', 'exists', 'not_exists'}
BOOLEAN_OPS = {'is_true', 'is_false', 'exists', 'not_exists'}
LIST_OPS = {'contains', 'not_contains', 'in', 'not_in', 'exists', 'not_exists'}

# field_name -> declared value type, used only to pick the allowed operator
# set above — the actual value extraction happens in conditions.py via a
# hardcoded dispatch table keyed by these exact same names.
CONDITION_FIELDS = {
    # Conversation
    'conversation.type': 'string',
    'conversation.status': 'string',
    'conversation.priority': 'string',
    'conversation.queue_id': 'string',
    'conversation.team_id': 'string',
    'conversation.assignee_id': 'string',
    'conversation.assigned': 'boolean',
    'conversation.created_at': 'datetime',
    'conversation.waiting_minutes': 'number',
    'conversation.message_count': 'number',
    'conversation.customer_message_count': 'number',
    'conversation.operator_message_count': 'number',
    'conversation.rating': 'number',
    'conversation.sla_state': 'string',
    # Customer context
    'customer.tags': 'list',
    'customer.score': 'number',
    'customer.order_count': 'number',
    'customer.total_spending': 'number',
    'customer.location': 'string',
    'customer.metadata': 'metadata',  # special-cased: requires a "path" key
    # Message (only populated for message-triggered events)
    'message.type': 'string',
    'message.content': 'string',
    'message.sender_type': 'string',
    'message.attachment_type': 'string',
    # Operational
    'operational.business_hours': 'boolean',
    'operational.agent_online': 'boolean',
    'operational.agent_at_capacity': 'boolean',
    'operational.queue_has_capacity': 'boolean',
    'operational.assignee_is_team_member': 'boolean',
}

TYPE_OPERATORS = {
    'string': STRING_OPS,
    'number': NUMBER_OPS,
    'datetime': DATETIME_OPS,
    'boolean': BOOLEAN_OPS,
    'list': LIST_OPS,
    'metadata': STRING_OPS | NUMBER_OPS | BOOLEAN_OPS,
}

VALUELESS_OPERATORS = {'exists', 'not_exists', 'is_true', 'is_false'}

# action_type -> {param_name: {"required": bool, "type": "workspace_id"|"str"|"int"|"any"}}
# "workspace_id" params reference another model's primary key and MUST be
# validated (by the caller, with the target workspace) to belong to the
# same workspace as the rule — see validate_actions()'s workspace checks.
ACTION_PARAM_SPECS = {
    'ASSIGN_TO_AGENT': {'agent_id': {'required': True, 'ref': 'user'}},
    'ASSIGN_TO_TEAM': {'team_id': {'required': True, 'ref': 'team'}},
    'ASSIGN_USING_QUEUE_STRATEGY': {'queue_id': {'required': True, 'ref': 'queue'}},
    'UNASSIGN': {},
    'RETURN_TO_QUEUE': {},
    'TRANSFER_TO_TEAM': {'team_id': {'required': True, 'ref': 'team'}, 'reason': {'required': False, 'type': str}},
    'ESCALATE': {'reason': {'required': False, 'type': str}},
    'SET_PRIORITY': {'priority': {'required': True, 'choices': ('LOW', 'NORMAL', 'HIGH', 'URGENT')}},
    'SET_STATUS': {'status': {'required': True, 'choices': ('OPEN', 'PENDING', 'CLOSED', 'WAITING_FOR_WORKSPACE', 'WAITING_FOR_PLATFORM', 'RESOLVED')}},
    'ADD_TAG': {'tag_id': {'required': True, 'ref': 'tag'}},
    'REMOVE_TAG': {'tag_id': {'required': True, 'ref': 'tag'}},
    'SEND_CUSTOMER_MESSAGE': {'template': {'required': True, 'type': str, 'max_len': 2000}},
    'CREATE_INTERNAL_NOTE': {'content': {'required': True, 'type': str, 'max_len': 4000}},
    'SEND_NOTIFICATION': {
        'title': {'required': True, 'type': str, 'max_len': 255},
        'target': {'required': False, 'choices': ('ASSIGNEE', 'TEAM_SUPERVISORS', 'WORKSPACE_ADMINS')},
    },
    'NOTIFY_SUPERVISOR': {'title': {'required': True, 'type': str, 'max_len': 255}},
    'REQUEST_RATING': {},
    'CLOSE_CONVERSATION': {},
    'REOPEN_CONVERSATION': {},
    'SCHEDULE_ACTION': {
        'delay_minutes': {'required': True, 'type': (int, float), 'min': 0, 'max': 60 * 24 * 14},
        'action': {'required': True, 'type': dict},
    },
    'CANCEL_SCHEDULED_ACTION': {'idempotency_key': {'required': False, 'type': str}},
}

# Actions that SCHEDULE_ACTION may itself wrap — deliberately a strict
# subset (no nested SCHEDULE_ACTION, no CANCEL_SCHEDULED_ACTION) so a
# scheduled job can never re-schedule itself indefinitely by construction.
SCHEDULABLE_ACTION_TYPES = set(ACTION_PARAM_SPECS) - {'SCHEDULE_ACTION', 'CANCEL_SCHEDULED_ACTION'}


def _payload_size_ok(value):
    try:
        return len(json.dumps(value)) <= MAX_PAYLOAD_BYTES
    except (TypeError, ValueError):
        return False


def validate_conditions(tree, depth=0, _counter=None):
    """Validate a condition tree structurally and against the field/operator
    registry. Raises rest_framework.exceptions.ValidationError. Does not
    check workspace-scoped IDs (conditions don't currently reference other
    workspace resources by ID beyond queue/team, which are matched by
    string equality against the event's own snapshot — nothing to dereference).
    """
    if _counter is None:
        _counter = {'n': 0}
    if depth > MAX_CONDITION_DEPTH:
        raise ValidationError({'conditions': f'Condition nesting exceeds the maximum depth of {MAX_CONDITION_DEPTH}.'})
    if not isinstance(tree, dict):
        raise ValidationError({'conditions': 'Each condition node must be an object.'})
    if not tree:
        return  # empty = always matches

    keys = set(tree.keys())
    if keys == {'all'} or keys == {'any'}:
        group_key = 'all' if 'all' in keys else 'any'
        items = tree[group_key]
        if not isinstance(items, list):
            raise ValidationError({'conditions': f'"{group_key}" must be a list of conditions.'})
        for item in items:
            _counter['n'] += 1
            if _counter['n'] > MAX_CONDITIONS_TOTAL:
                raise ValidationError({'conditions': f'Too many conditions (max {MAX_CONDITIONS_TOTAL}).'})
            validate_conditions(item, depth + 1, _counter)
        return
    if keys == {'not'}:
        _counter['n'] += 1
        if _counter['n'] > MAX_CONDITIONS_TOTAL:
            raise ValidationError({'conditions': f'Too many conditions (max {MAX_CONDITIONS_TOTAL}).'})
        validate_conditions(tree['not'], depth + 1, _counter)
        return

    # Leaf condition.
    if not {'field', 'operator'} <= keys:
        raise ValidationError({'conditions': 'A leaf condition needs "field" and "operator" (and usually "value").'})
    field = tree['field']
    operator = tree['operator']
    if field not in CONDITION_FIELDS:
        raise ValidationError({'conditions': f'Unknown condition field "{field}".'})
    field_type = CONDITION_FIELDS[field]
    allowed_ops = TYPE_OPERATORS[field_type]
    if operator not in allowed_ops:
        raise ValidationError({'conditions': f'Operator "{operator}" is not valid for field "{field}".'})
    if operator not in VALUELESS_OPERATORS and 'value' not in tree:
        raise ValidationError({'conditions': f'Operator "{operator}" on "{field}" requires a "value".'})
    if field == 'customer.metadata':
        path = tree.get('path')
        if not isinstance(path, str) or not path or len(path) > 64 or not path.replace('_', '').isalnum():
            raise ValidationError({'conditions': 'customer.metadata conditions require an alphanumeric "path".'})
    if operator in ('in', 'not_in') and not isinstance(tree.get('value'), list):
        raise ValidationError({'conditions': f'Operator "{operator}" requires a list "value".'})


def validate_actions(actions, workspace=None, *, allow_schedule=True):
    """Validate an action list structurally, against the registry, and
    (when `workspace` is given) that every workspace-scoped reference
    (team_id/queue_id/tag_id/agent_id) actually belongs to that workspace —
    this is the "protected IDs are workspace-validated" requirement.
    """
    if not isinstance(actions, list):
        raise ValidationError({'actions': 'actions must be a list.'})
    if len(actions) > MAX_ACTIONS_PER_RULE:
        raise ValidationError({'actions': f'Too many actions (max {MAX_ACTIONS_PER_RULE}).'})
    for i, action in enumerate(actions):
        _validate_one_action(action, i, workspace, allow_schedule=allow_schedule)


def _validate_one_action(action, index, workspace, *, allow_schedule):
    if not isinstance(action, dict):
        raise ValidationError({'actions': f'Action #{index} must be an object.'})
    action_type = action.get('type')
    if not allow_schedule:
        valid_types = SCHEDULABLE_ACTION_TYPES
    else:
        valid_types = set(ACTION_PARAM_SPECS)
    if action_type not in valid_types:
        raise ValidationError({'actions': f'Unknown or disallowed action type "{action_type}" at #{index}.'})
    params = action.get('params') or {}
    if not isinstance(params, dict):
        raise ValidationError({'actions': f'Action #{index} params must be an object.'})
    spec = ACTION_PARAM_SPECS[action_type]
    for name, rule in spec.items():
        if rule.get('required') and name not in params:
            raise ValidationError({'actions': f'Action #{index} ({action_type}) missing required param "{name}".'})
        if name not in params:
            continue
        value = params[name]
        if 'choices' in rule and value not in rule['choices']:
            raise ValidationError({'actions': f'Action #{index} param "{name}" must be one of {rule["choices"]}.'})
        if 'type' in rule and not isinstance(value, rule['type']):
            raise ValidationError({'actions': f'Action #{index} param "{name}" has the wrong type.'})
        if 'max_len' in rule and isinstance(value, str) and len(value) > rule['max_len']:
            raise ValidationError({'actions': f'Action #{index} param "{name}" is too long (max {rule["max_len"]}).'})
        if 'min' in rule and isinstance(value, (int, float)) and value < rule['min']:
            raise ValidationError({'actions': f'Action #{index} param "{name}" is below the minimum ({rule["min"]}).'})
        if 'max' in rule and isinstance(value, (int, float)) and value > rule['max']:
            raise ValidationError({'actions': f'Action #{index} param "{name}" is above the maximum ({rule["max"]}).'})
        if rule.get('ref') and workspace is not None:
            _validate_workspace_reference(rule['ref'], value, workspace, index, name)
    unknown = set(params) - set(spec)
    if unknown:
        raise ValidationError({'actions': f'Action #{index} ({action_type}) has unsupported params: {sorted(unknown)}.'})

    if action_type == 'SCHEDULE_ACTION':
        inner = params.get('action') or {}
        _validate_one_action(inner, index, workspace, allow_schedule=False)


def _validate_workspace_reference(ref_kind, value, workspace, index, name):
    from django.core.exceptions import ValidationError as DjangoValidationError

    try:
        if ref_kind == 'team':
            from teams.models import Team
            exists = Team.objects.filter(id=value, workspace=workspace).exists()
        elif ref_kind == 'queue':
            from queues.models import Queue
            exists = Queue.objects.filter(id=value, workspace=workspace).exists()
        elif ref_kind == 'tag':
            from customer_context.models import Tag
            exists = Tag.objects.filter(id=value, workspace=workspace).exists()
        elif ref_kind == 'user':
            exists = workspace.memberships.filter(user_id=value).exists()
        else:
            exists = False
    except (DjangoValidationError, ValueError, TypeError):
        exists = False
    if not exists:
        raise ValidationError({'actions': f'Action #{index} param "{name}" does not reference a resource in this workspace.'})


def validate_rule_payload(conditions, actions, workspace=None):
    """Top-level entry point used by the serializer: full structural +
    size + workspace-reference validation for one rule's conditions/actions.
    """
    if not _payload_size_ok(conditions):
        raise ValidationError({'conditions': f'Conditions payload exceeds {MAX_PAYLOAD_BYTES} bytes.'})
    if not _payload_size_ok(actions):
        raise ValidationError({'actions': f'Actions payload exceeds {MAX_PAYLOAD_BYTES} bytes.'})
    validate_conditions(conditions or {})
    validate_actions(actions or [], workspace=workspace)
