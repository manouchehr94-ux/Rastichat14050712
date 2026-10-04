"""Safe variable interpolation for macro reply/internal-note actions — same
whitelist-regex approach as automations/templating.py (never .format()/eval,
never dotted attribute traversal), restricted to exactly the allowlist in
spec section 13. Anything not in `ALLOWED_VARIABLES` — including any
attempt at a secret/token/password-shaped name — is left as literal
`{token}` text, never evaluated and never an error.
"""
import re

_VARIABLE_PATTERN = re.compile(r'\{(\w+)\}')
MAX_TEMPLATE_LENGTH = 2000

ALLOWED_VARIABLES = frozenset({
    'customer_name', 'store_name', 'conversation_id', 'agent_name', 'queue_name', 'team_name',
    'article_title', 'order_number', 'product_name',
})


class MacroTemplateError(Exception):
    pass


def build_macro_variable_context(conversation, *, article_title=None):
    visitor = conversation.visitor
    project = visitor.project if visitor and visitor.project_id else None
    queue = conversation.queue
    team = conversation.team
    agent = conversation.assigned_to

    context = {
        'customer_name': (visitor.name if visitor and visitor.name else 'مشتری'),
        'store_name': project.name if project else conversation.workspace.name,
        'conversation_id': str(conversation.id),
        'queue_name': queue.name if queue else '',
        'team_name': team.name if team else '',
        'agent_name': (agent.display_name or agent.email.split('@')[0]) if agent else '',
    }
    if article_title:
        context['article_title'] = article_title
    order = _latest_order(visitor)
    if order is not None:
        context['order_number'] = order.external_order_id or str(order.id)
        context['product_name'] = order.product_name
    return context


def _latest_order(visitor):
    if visitor is None:
        return None
    from customer_context.models import CustomerOrder
    return CustomerOrder.objects.filter(visitor=visitor).order_by('-ordered_at').first()


def resolve_macro_template(template, conversation, *, article_title=None):
    """Unknown/unsupplied variables are left as literal `{token}` text —
    never evaluated, never an error at execution time.
    """
    if len(template) > MAX_TEMPLATE_LENGTH:
        raise MacroTemplateError('Template exceeds the maximum length')
    context = build_macro_variable_context(conversation, article_title=article_title)

    def _replace(match):
        name = match.group(1)
        if name in ALLOWED_VARIABLES and name in context:
            return str(context[name])
        return match.group(0)

    return _VARIABLE_PATTERN.sub(_replace, template)
