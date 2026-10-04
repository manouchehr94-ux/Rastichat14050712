"""Declarative condition evaluation. Every field name is resolved through a
hardcoded dispatch table (FIELD_RESOLVERS) — there is no getattr()-by-string
against a model or any other form of dynamic attribute traversal anywhere
in this module. Unknown fields are rejected earlier, at schema validation
time (automations/schema.py), and this module double-checks defensively.
"""
from django.db.models import Sum
from django.utils import timezone

from .schema import CONDITION_FIELDS

_MISSING = object()

# Fields whose free-text values get Persian/case normalization before
# string comparisons (see normalize_keyword_text) — enum-like fields
# (status/type/priority/...) are compared exactly instead.
_NORMALIZED_TEXT_FIELDS = {'message.content', 'customer.location'}


def normalize_keyword_text(text):
    """Whitespace-normalized, casefolded, Persian-character-normalized
    (ی/ي, ک/ك) text — used for keyword matching per spec section 15.
    """
    if text is None:
        return ''
    text = str(text).replace('ي', 'ی').replace('ك', 'ک')  # ي->ی, ك->ک
    return ' '.join(text.split()).casefold()


class ConditionContext:
    """Everything a condition might need, resolved once per event and
    reused across every rule evaluated against it (no repeated
    per-rule customer-context fetches).
    """
    def __init__(self, event, conversation):
        self.event = event
        self.conv = conversation
        self._profile = None
        self._profile_loaded = False

    @property
    def profile(self):
        if not self._profile_loaded:
            self._profile_loaded = True
            if self.conv and self.conv.visitor_id:
                self._profile = getattr(self.conv.visitor, 'profile', None)
        return self._profile


def _conv_field(name):
    def getter(ctx):
        if not ctx.conv:
            return _MISSING
        return getattr(ctx.conv, name)
    return getter


def _sla_state(ctx):
    if not ctx.conv:
        return _MISSING
    sla = getattr(ctx.conv, 'sla', None)
    if not sla:
        return 'none'
    if sla.first_response_breached_at or sla.next_response_breached_at or sla.resolution_breached_at:
        return 'breached'
    if sla.first_response_approaching_notified_at or sla.next_response_approaching_notified_at or sla.resolution_approaching_notified_at:
        return 'approaching'
    return 'on_track'


def _waiting_minutes(ctx):
    if not ctx.conv:
        return _MISSING
    last_msg = ctx.conv.messages.order_by('-created_at').first()
    if not last_msg or last_msg.sender_type != 'VISITOR':
        return _MISSING
    return (timezone.now() - last_msg.created_at).total_seconds() / 60


def _message_count(ctx):
    return ctx.conv.messages.count() if ctx.conv else _MISSING


def _customer_message_count(ctx):
    return ctx.conv.messages.filter(sender_type='VISITOR').count() if ctx.conv else _MISSING


def _operator_message_count(ctx):
    if not ctx.conv:
        return _MISSING
    return ctx.conv.messages.filter(sender_type='USER').exclude(message_type='INTERNAL_NOTE').count()


def _customer_tags(ctx):
    if not ctx.conv:
        return _MISSING
    return list(ctx.conv.conversation_tags.values_list('tag__name', flat=True))


def _customer_score(ctx):
    profile = ctx.profile
    if not profile or profile.score is None:
        return _MISSING
    return float(profile.score)


def _customer_order_count(ctx):
    if not ctx.conv or not ctx.conv.visitor_id:
        return _MISSING
    return ctx.conv.visitor.orders.count()


def _customer_total_spending(ctx):
    if not ctx.conv or not ctx.conv.visitor_id:
        return _MISSING
    agg = ctx.conv.visitor.orders.aggregate(total=Sum('price'))
    return float(agg['total'] or 0)


def _customer_location(ctx):
    profile = ctx.profile
    return profile.location if profile and profile.location else _MISSING


def _op_business_hours(ctx):
    from zoneinfo import ZoneInfo
    from sla.models import BusinessCalendar
    from sla.services import _working_windows_for_date

    calendar = None
    if ctx.conv:
        sla = getattr(ctx.conv, 'sla', None)
        if sla and sla.policy_id and sla.policy.calendar_id:
            calendar = sla.policy.calendar
    if calendar is None:
        calendar = BusinessCalendar.objects.filter(workspace_id=ctx.event.workspace_id).first()
    if calendar is None:
        return True
    now = timezone.now()
    local_date = now.astimezone(ZoneInfo(calendar.timezone)).date()
    windows = _working_windows_for_date(calendar, local_date)
    return any(start <= now <= end for start, end in windows)


def _op_agent_online(ctx):
    from accounts.models import OperatorPresence
    if not ctx.conv or not ctx.conv.assigned_to_id:
        return False
    presence = getattr(ctx.conv.assigned_to, 'presence', None)
    if not presence:
        return False
    return presence.effective_status() not in (OperatorPresence.Status.OFFLINE, OperatorPresence.Status.DO_NOT_DISTURB)


def _op_agent_at_capacity(ctx):
    if not ctx.conv or not ctx.conv.assigned_to_id:
        return _MISSING
    presence = getattr(ctx.conv.assigned_to, 'presence', None)
    if not presence:
        return False
    return presence.is_at_capacity()


def _op_queue_has_capacity(ctx):
    from queues.services import _eligible_agents
    if not ctx.conv or not ctx.conv.queue_id:
        return _MISSING
    return bool(_eligible_agents(ctx.conv.queue))


def _op_assignee_is_team_member(ctx):
    from teams.models import TeamMembership
    if not ctx.conv or not ctx.conv.team_id or not ctx.conv.assigned_to_id:
        return _MISSING
    return TeamMembership.objects.filter(team_id=ctx.conv.team_id, user_id=ctx.conv.assigned_to_id, is_active=True).exists()


FIELD_RESOLVERS = {
    'conversation.type': _conv_field('type'),
    'conversation.status': _conv_field('status'),
    'conversation.priority': _conv_field('priority'),
    'conversation.queue_id': lambda ctx: str(ctx.conv.queue_id) if ctx.conv and ctx.conv.queue_id else _MISSING,
    'conversation.team_id': lambda ctx: str(ctx.conv.team_id) if ctx.conv and ctx.conv.team_id else _MISSING,
    'conversation.assignee_id': lambda ctx: str(ctx.conv.assigned_to_id) if ctx.conv and ctx.conv.assigned_to_id else _MISSING,
    'conversation.assigned': lambda ctx: bool(ctx.conv and ctx.conv.assigned_to_id),
    'conversation.created_at': _conv_field('created_at'),
    'conversation.waiting_minutes': _waiting_minutes,
    'conversation.message_count': _message_count,
    'conversation.customer_message_count': _customer_message_count,
    'conversation.operator_message_count': _operator_message_count,
    'conversation.rating': _conv_field('rating'),
    'conversation.sla_state': _sla_state,
    'customer.tags': _customer_tags,
    'customer.score': _customer_score,
    'customer.order_count': _customer_order_count,
    'customer.total_spending': _customer_total_spending,
    'customer.location': _customer_location,
    'customer.metadata': lambda ctx: (ctx.profile.metadata if ctx.profile else {}) or {},
    'message.type': lambda ctx: ctx.event.payload.get('message_type', _MISSING),
    'message.content': lambda ctx: ctx.event.payload.get('content', _MISSING),
    'message.sender_type': lambda ctx: ctx.event.payload.get('sender_type', _MISSING),
    'message.attachment_type': lambda ctx: ctx.event.payload.get('attachment_type', _MISSING),
    'operational.business_hours': _op_business_hours,
    'operational.agent_online': _op_agent_online,
    'operational.agent_at_capacity': _op_agent_at_capacity,
    'operational.queue_has_capacity': _op_queue_has_capacity,
    'operational.assignee_is_team_member': _op_assignee_is_team_member,
}


def _coerce_datetime(value):
    if isinstance(value, str):
        from django.utils.dateparse import parse_datetime
        parsed = parse_datetime(value)
        return parsed
    return value


def _apply_operator(operator, field, actual, expected):
    if operator == 'exists':
        if actual is _MISSING or actual is None:
            return False
        if isinstance(actual, (list, dict)) and not actual:
            return False
        return True
    if operator == 'not_exists':
        return not _apply_operator('exists', field, actual, expected)
    if operator == 'is_true':
        return actual is not _MISSING and bool(actual) is True
    if operator == 'is_false':
        return actual is not _MISSING and bool(actual) is False

    if actual is _MISSING or actual is None:
        return False

    normalize = field in _NORMALIZED_TEXT_FIELDS

    if operator == 'equals':
        if normalize:
            return normalize_keyword_text(actual) == normalize_keyword_text(expected)
        return actual == expected
    if operator == 'not_equals':
        return not _apply_operator('equals', field, actual, expected)
    if operator == 'in':
        return actual in (expected or [])
    if operator == 'not_in':
        return actual not in (expected or [])
    if operator == 'contains':
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return normalize_keyword_text(expected) in normalize_keyword_text(actual)
    if operator == 'not_contains':
        return not _apply_operator('contains', field, actual, expected)
    if operator == 'starts_with':
        return normalize_keyword_text(actual).startswith(normalize_keyword_text(expected))
    if operator == 'ends_with':
        return normalize_keyword_text(actual).endswith(normalize_keyword_text(expected))
    if operator in ('greater_than', 'greater_than_or_equal', 'less_than', 'less_than_or_equal'):
        try:
            a, e = float(actual), float(expected)
        except (TypeError, ValueError):
            return False
        return {
            'greater_than': a > e, 'greater_than_or_equal': a >= e,
            'less_than': a < e, 'less_than_or_equal': a <= e,
        }[operator]
    if operator in ('before', 'after'):
        a, e = _coerce_datetime(actual), _coerce_datetime(expected)
        if a is None or e is None:
            return False
        return a < e if operator == 'before' else a > e
    return False


def resolve_field(ctx, field, path=None):
    resolver = FIELD_RESOLVERS.get(field)
    if resolver is None:
        return _MISSING
    value = resolver(ctx)
    if field == 'customer.metadata':
        if value is _MISSING or not isinstance(value, dict):
            return _MISSING
        return value.get(path, _MISSING)
    return value


def evaluate_condition(node, ctx):
    """Evaluate one condition-tree node (see schema.py for the grammar).
    Assumes the tree already passed validate_conditions(); still defensive
    against unknown fields (treated as never-matching, not an error, since
    this runs at evaluation time where raising would break the trigger).
    """
    if not node:
        return True
    if 'all' in node:
        return all(evaluate_condition(child, ctx) for child in node['all'])
    if 'any' in node:
        return any(evaluate_condition(child, ctx) for child in node['any'])
    if 'not' in node:
        return not evaluate_condition(node['not'], ctx)

    field = node.get('field')
    operator = node.get('operator')
    if field not in CONDITION_FIELDS:
        return False
    actual = resolve_field(ctx, field, path=node.get('path'))
    expected = node.get('value')
    return _apply_operator(operator, field, actual, expected)
