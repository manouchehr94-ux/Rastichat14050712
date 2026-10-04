"""Strict, declarative schema validation for Macro.actions — the same
design as automations/schema.py: every action type/param name/reference
must exactly match a fixed, hardcoded registry, so a macro can never
execute dynamic code or reference a resource outside its own workspace.

Action list grammar (JSON): a list of {"type": "<ACTION_TYPE>", "params": {...}}
"""
import json

from rest_framework.exceptions import ValidationError

SCHEMA_VERSION = 1

MAX_ACTIONS_PER_MACRO = 20
MAX_PAYLOAD_BYTES = 20_000

# action_type -> {param_name: {"required": bool, ...}} — see automations/schema.py
# for the exact same rule-dict shape (choices/type/max_len/min/max/ref).
ACTION_PARAM_SPECS = {
    'SEND_REPLY': {'template': {'required': True, 'type': str, 'max_len': 2000}},
    'SEND_ARTICLE': {'article_id': {'required': True, 'ref': 'kb_article'}},
    'ADD_TAG': {'tag_id': {'required': True, 'ref': 'tag'}},
    'REMOVE_TAG': {'tag_id': {'required': True, 'ref': 'tag'}},
    'SET_PRIORITY': {'priority': {'required': True, 'choices': ('LOW', 'NORMAL', 'HIGH', 'URGENT')}},
    'SET_STATUS': {'status': {'required': True, 'choices': (
        'OPEN', 'PENDING', 'CLOSED', 'WAITING_FOR_WORKSPACE', 'WAITING_FOR_PLATFORM', 'RESOLVED',
    )}},
    'ASSIGN_TO_AGENT': {'agent_id': {'required': True, 'ref': 'user'}},
    'ASSIGN_TO_TEAM': {'team_id': {'required': True, 'ref': 'team'}},
    'RETURN_TO_QUEUE': {},
    'TRANSFER_TO_TEAM': {'team_id': {'required': True, 'ref': 'team'}, 'reason': {'required': False, 'type': str, 'max_len': 500}},
    'CREATE_INTERNAL_NOTE': {'content': {'required': True, 'type': str, 'max_len': 4000}},
    'REQUEST_RATING': {},
    'CLOSE_CONVERSATION': {},
    'REOPEN_CONVERSATION': {},
}


def _payload_size_ok(value):
    try:
        return len(json.dumps(value)) <= MAX_PAYLOAD_BYTES
    except (TypeError, ValueError):
        return False


def validate_actions(actions, workspace=None):
    if not isinstance(actions, list):
        raise ValidationError({'actions': 'actions must be a list.'})
    if not actions:
        raise ValidationError({'actions': 'A macro must have at least one action.'})
    if len(actions) > MAX_ACTIONS_PER_MACRO:
        raise ValidationError({'actions': f'Too many actions (max {MAX_ACTIONS_PER_MACRO}).'})
    if not _payload_size_ok(actions):
        raise ValidationError({'actions': f'Actions payload exceeds {MAX_PAYLOAD_BYTES} bytes.'})
    for i, action in enumerate(actions):
        _validate_one_action(action, i, workspace)


def _validate_one_action(action, index, workspace):
    if not isinstance(action, dict):
        raise ValidationError({'actions': f'Action #{index} must be an object.'})
    action_type = action.get('type')
    if action_type not in ACTION_PARAM_SPECS:
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
        if rule.get('ref') and workspace is not None:
            _validate_workspace_reference(rule['ref'], value, workspace, index, name)
    unknown = set(params) - set(spec)
    if unknown:
        raise ValidationError({'actions': f'Action #{index} ({action_type}) has unsupported params: {sorted(unknown)}.'})


def _validate_workspace_reference(ref_kind, value, workspace, index, name):
    from django.core.exceptions import ValidationError as DjangoValidationError

    try:
        if ref_kind == 'team':
            from teams.models import Team
            exists = Team.objects.filter(id=value, workspace=workspace).exists()
        elif ref_kind == 'tag':
            from customer_context.models import Tag
            exists = Tag.objects.filter(id=value, workspace=workspace).exists()
        elif ref_kind == 'user':
            exists = workspace.memberships.filter(user_id=value).exists()
        elif ref_kind == 'kb_article':
            from knowledge_base.models import KnowledgeBaseArticle
            exists = KnowledgeBaseArticle.objects.filter(id=value, workspace=workspace).exists()
        else:
            exists = False
    except (DjangoValidationError, ValueError, TypeError):
        exists = False
    if not exists:
        raise ValidationError({'actions': f'Action #{index} param "{name}" does not reference a resource in this workspace.'})
