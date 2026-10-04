import re

# Only these tokens are ever substituted. Anything else inside {..} is left
# completely untouched — never evaluated, never attribute-accessed — so an
# unrecognized variable name can never leak internal server state.
_TEMPLATE_VARIABLE_PATTERN = re.compile(r'\{(\w+)\}')


def build_variable_context(conversation, agent):
    visitor = conversation.visitor
    project = visitor.project if visitor and visitor.project_id else None
    return {
        'customer_name': (visitor.name if visitor and visitor.name else 'مشتری'),
        'agent_name': agent.display_name or agent.email.split('@')[0],
        'store_name': project.name if project else conversation.workspace.name,
        'conversation_id': str(conversation.id),
    }


def resolve_quick_reply(body, context):
    def _replace(match):
        name = match.group(1)
        return str(context[name]) if name in context else match.group(0)
    return _TEMPLATE_VARIABLE_PATTERN.sub(_replace, body)
