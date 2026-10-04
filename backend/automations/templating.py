"""Safe variable interpolation for automated customer messages — the same
whitelist-regex approach as collaboration.services.resolve_quick_reply
(never .format()/eval, so an unrecognized token can never trigger attribute
access or leak internal state), extended with the automation-specific
variables from spec section 5.
"""
import re

_VARIABLE_PATTERN = re.compile(r'\{(\w+)\}')
MAX_TEMPLATE_LENGTH = 2000


class TemplateError(Exception):
    pass


def build_automation_variable_context(conversation):
    visitor = conversation.visitor
    project = visitor.project if visitor and visitor.project_id else None
    queue = conversation.queue
    team = conversation.team
    agent = conversation.assigned_to

    return {
        'customer_name': (visitor.name if visitor and visitor.name else 'مشتری'),
        'store_name': project.name if project else conversation.workspace.name,
        'conversation_id': str(conversation.id),
        'queue_name': queue.name if queue else '',
        'team_name': team.name if team else '',
        'agent_name': (agent.display_name or agent.email.split('@')[0]) if agent else '',
        'business_hours_summary': _business_hours_summary(conversation),
    }


def _business_hours_summary(conversation):
    from sla.models import BusinessCalendar
    calendar = None
    sla = getattr(conversation, 'sla', None)
    if sla and sla.policy_id and sla.policy.calendar_id:
        calendar = sla.policy.calendar
    if calendar is None:
        calendar = BusinessCalendar.objects.filter(workspace=conversation.workspace).first()
    if calendar is None:
        return '۲۴ ساعته'
    intervals = list(calendar.working_intervals.order_by('weekday', 'start_time')[:1])
    if not intervals:
        return calendar.name
    first = intervals[0]
    return f'{calendar.name}: {first.start_time.strftime("%H:%M")}-{first.end_time.strftime("%H:%M")}'


def resolve_template(template, conversation):
    """Unknown variables are left as literal `{token}` text — never
    evaluated, never treated as an error at send time (rules are validated
    for length at save time; a genuinely malformed template should have
    been caught then, not silently drop the whole automated message here).
    """
    if len(template) > MAX_TEMPLATE_LENGTH:
        raise TemplateError('Template exceeds the maximum length')
    context = build_automation_variable_context(conversation)

    def _replace(match):
        name = match.group(1)
        return str(context[name]) if name in context else match.group(0)

    return _VARIABLE_PATTERN.sub(_replace, template)
