"""Zero-side-effect rule dry-run (spec section 12).

This module deliberately never imports `execute_action` or `process_event`
— there is no code path by which a simulation request can reach a real
mutation, a real message send, a real notification, a real assignment, or
a real scheduled job. It only reuses the pure, read-only pieces of the
condition engine (`evaluate_condition` / `FIELD_RESOLVERS` via
`resolve_field`) and reflects the already-validated `actions` list back as
a preview.
"""
import uuid

from .conditions import ConditionContext, resolve_field
from .events import TriggerEvent


def _trace_condition(node, ctx):
    """Mirrors conditions.evaluate_condition but also returns a structural
    trace of which branch/leaf matched, for the "show which conditions
    matched" requirement.
    """
    if not node:
        return True, {'type': 'empty', 'result': True}

    if 'all' in node:
        children = [_trace_condition(child, ctx) for child in node['all']]
        result = all(r for r, _ in children)
        return result, {'type': 'all', 'result': result, 'children': [t for _, t in children]}

    if 'any' in node:
        children = [_trace_condition(child, ctx) for child in node['any']]
        result = any(r for r, _ in children)
        return result, {'type': 'any', 'result': result, 'children': [t for _, t in children]}

    if 'not' in node:
        inner_result, inner_trace = _trace_condition(node['not'], ctx)
        result = not inner_result
        return result, {'type': 'not', 'result': result, 'child': inner_trace}

    from .schema import CONDITION_FIELDS
    field = node.get('field')
    operator = node.get('operator')
    if field not in CONDITION_FIELDS:
        return False, {'type': 'leaf', 'result': False, 'field': field, 'operator': operator, 'error': 'unknown_field'}

    from .conditions import _apply_operator, _MISSING
    actual = resolve_field(ctx, field, path=node.get('path'))
    expected = node.get('value')
    result = _apply_operator(operator, field, actual, expected)
    return result, {
        'type': 'leaf', 'result': result, 'field': field, 'operator': operator, 'value': expected,
        'actual': None if actual is _MISSING else actual,
    }


def simulate(conditions, actions, conversation, trigger_type, *, payload=None, actor_id=None):
    """Pure dry-run: evaluates `conditions` against `conversation` and
    reports whether it matches plus a preview of the actions that *would*
    run — never calling any action handler, so nothing is mutated,
    messaged, notified, assigned, or scheduled.
    """
    event = TriggerEvent(
        event_type=trigger_type,
        workspace_id=str(conversation.workspace_id) if conversation else '',
        event_id=uuid.uuid4(),
        conversation_id=str(conversation.id) if conversation else None,
        actor_id=str(actor_id) if actor_id else None,
        payload=payload or {},
        source='simulation',
    )
    ctx = ConditionContext(event, conversation)
    matched, trace = _trace_condition(conditions or {}, ctx)

    would_run_actions = []
    if matched:
        for i, action in enumerate(actions or []):
            would_run_actions.append({
                'index': i, 'type': action.get('type'), 'params': action.get('params', {}),
            })

    return {
        'matched': matched,
        'condition_trace': trace,
        'would_run_actions': would_run_actions,
    }


def simulate_rule(rule, conversation, *, payload=None, actor_id=None):
    """Convenience wrapper for simulating an already-persisted rule."""
    return simulate(rule.conditions, rule.actions, conversation, rule.trigger_type, payload=payload, actor_id=actor_id)
