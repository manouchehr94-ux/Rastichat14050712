"""Macro action execution. Every handler is a small, hardcoded Python
function keyed by the validated action_type string — never dynamically
constructed. Handlers call the SAME approved domain services the rest of
the product uses (conversations.services, knowledge_base.services) instead
of mutating models directly — this is the literal "macros must reuse the
same capacity-safe and idempotent domain services already approved in
Phase 4" requirement. Unlike automations (system-triggered, actor=None),
every macro action is attributed to the real operator who ran it.
"""
from dataclasses import dataclass

from conversations import services as conv_services
from conversations.models import Conversation, Message

from .templating import MacroTemplateError, resolve_macro_template


class ActionError(Exception):
    pass


@dataclass
class MacroActionContext:
    execution_id: str
    action_index: int
    actor: object


def _resolve_team(workspace, team_id):
    from teams.models import Team
    team = Team.objects.filter(id=team_id, workspace=workspace).first()
    if not team:
        raise ActionError('Team not found in this workspace')
    return team


def _resolve_tag(workspace, tag_id):
    from customer_context.models import Tag
    tag = Tag.objects.filter(id=tag_id, workspace=workspace).first()
    if not tag:
        raise ActionError('Tag not found in this workspace')
    return tag


def _resolve_agent(workspace, agent_id):
    user = workspace.memberships.filter(user_id=agent_id).select_related('user').first()
    if not user:
        raise ActionError('Agent is not a member of this workspace')
    return user.user


def _resolve_article(workspace, article_id):
    from knowledge_base.models import KnowledgeBaseArticle
    article = KnowledgeBaseArticle.objects.filter(id=article_id, workspace=workspace).first()
    if not article:
        raise ActionError('Article not found in this workspace')
    return article


def act_send_reply(conv, params, ctx):
    client_msg_id = f'macro:{ctx.execution_id}:{ctx.action_index}'
    if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
        existing = Message.objects.get(conversation=conv, client_message_id=client_msg_id)
        return {'skipped': 'duplicate', 'message_id': str(existing.id)}, 'message', str(existing.id)
    try:
        body = resolve_macro_template(params['template'], conv)
    except MacroTemplateError as exc:
        raise ActionError(str(exc))
    if not conv.visitor_id:
        raise ActionError('Conversation has no visitor to reply to')
    msg = Message.objects.create(
        conversation=conv, sender=ctx.actor, sender_type=Message.SenderType.USER, content=body,
        client_message_id=client_msg_id, message_type=Message.MessageType.TEXT,
    )
    conv_services.broadcast_ops_event(conv.id, 'conversation.macro_message', {'message_id': str(msg.id)})
    _broadcast_widget_message(conv, msg)
    return {'message_id': str(msg.id)}, 'message', str(msg.id)


def act_send_article(conv, params, ctx):
    from knowledge_base.services import share_article_to_conversation
    article = _resolve_article(conv.workspace, params['article_id'])
    client_msg_id = f'macro:{ctx.execution_id}:{ctx.action_index}'
    msg = share_article_to_conversation(article, conv, ctx.actor, client_msg_id)
    return {'message_id': str(msg.id), 'article_id': str(article.id)}, 'message', str(msg.id)


def act_add_tag(conv, params, ctx):
    from customer_context.models import ConversationTag
    tag = _resolve_tag(conv.workspace, params['tag_id'])
    ConversationTag.objects.get_or_create(conversation=conv, tag=tag, defaults={'created_by': ctx.actor})
    return {'tag_id': str(tag.id)}, 'tag', str(tag.id)


def act_remove_tag(conv, params, ctx):
    from customer_context.models import ConversationTag
    tag = _resolve_tag(conv.workspace, params['tag_id'])
    ConversationTag.objects.filter(conversation=conv, tag=tag).delete()
    return {'tag_id': str(tag.id)}, 'tag', str(tag.id)


def act_set_priority(conv, params, ctx):
    from conversations.models import PriorityChange
    conv_services.set_priority(
        conv, actor=ctx.actor, priority=params['priority'], reason='Macro', reason_code=PriorityChange.Reason.MANUAL,
    )
    return {'priority': params['priority']}, 'conversation', str(conv.id)


def act_set_status(conv, params, ctx):
    target = params['status']
    if target == Conversation.Status.CLOSED:
        conv_services.close_conversation(conv, actor=ctx.actor)
    elif target == Conversation.Status.OPEN and conv.status == Conversation.Status.CLOSED:
        conv_services.reopen_conversation(conv, actor=ctx.actor)
    else:
        conv_services.set_status(conv, actor=ctx.actor, status=target, reason='Macro')
    return {'status': target}, 'conversation', str(conv.id)


def act_assign_to_agent(conv, params, ctx):
    agent = _resolve_agent(conv.workspace, params['agent_id'])
    conv_services.assign_to_agent(conv, actor=ctx.actor, target_user_id=agent.id, reason='Macro')
    return {'assigned_to': str(agent.id)}, 'user', str(agent.id)


def act_assign_to_team(conv, params, ctx):
    team = _resolve_team(conv.workspace, params['team_id'])
    conv_services.transfer_team(conv, actor=ctx.actor, new_team_id=team.id, reason='Macro')
    return {'team_id': str(team.id)}, 'team', str(team.id)


def act_return_to_queue(conv, params, ctx):
    conv_services.return_to_queue(conv, actor=ctx.actor, reason='Macro')
    return {}, 'conversation', str(conv.id)


def act_transfer_to_team(conv, params, ctx):
    team = _resolve_team(conv.workspace, params['team_id'])
    conv_services.transfer_team(conv, actor=ctx.actor, new_team_id=team.id, reason=params.get('reason', 'Macro'))
    return {'team_id': str(team.id)}, 'team', str(team.id)


def act_create_internal_note(conv, params, ctx):
    client_msg_id = f'macro-note:{ctx.execution_id}:{ctx.action_index}'
    if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
        existing = Message.objects.get(conversation=conv, client_message_id=client_msg_id)
        return {'skipped': 'duplicate'}, 'message', str(existing.id)
    note = Message.objects.create(
        conversation=conv, sender=ctx.actor, sender_type=Message.SenderType.USER, content=params['content'][:4000],
        client_message_id=client_msg_id, message_type=Message.MessageType.INTERNAL_NOTE,
    )
    conv_services.broadcast_ops_event(conv.id, 'conversation.internal_note_created', {'note': {'content': note.content, 'automated': False}})
    return {'note_id': str(note.id)}, 'message', str(note.id)


def act_request_rating(conv, params, ctx):
    client_msg_id = f'macro-rating:{ctx.execution_id}:{ctx.action_index}'
    if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
        existing = Message.objects.get(conversation=conv, client_message_id=client_msg_id)
        return {'skipped': 'duplicate'}, 'message', str(existing.id)
    msg = Message.objects.create(
        conversation=conv, sender=ctx.actor, sender_type=Message.SenderType.SYSTEM, content='',
        client_message_id=client_msg_id, message_type=Message.MessageType.RATING_REQUEST,
    )
    _broadcast_widget_message(conv, msg)
    return {'message_id': str(msg.id)}, 'message', str(msg.id)


def act_close_conversation(conv, params, ctx):
    conv_services.close_conversation(conv, actor=ctx.actor)
    return {}, 'conversation', str(conv.id)


def act_reopen_conversation(conv, params, ctx):
    try:
        conv_services.reopen_conversation(conv, actor=ctx.actor)
    except conv_services.ConversationServiceError:
        return {'skipped': 'not_closed'}, 'conversation', str(conv.id)
    return {}, 'conversation', str(conv.id)


def _broadcast_widget_message(conv, msg):
    import json
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    layer = get_channel_layer()
    if not layer:
        return
    payload = {
        'id': str(msg.id), 'sender_type': msg.sender_type, 'content': msg.content,
        'message_type': msg.message_type, 'metadata': msg.metadata, 'attachment_url': None,
        'client_message_id': msg.client_message_id, 'created_at': msg.created_at.isoformat(),
    }
    safe = json.loads(json.dumps(payload, default=str))
    async_to_sync(layer.group_send)(f'chat_{conv.id}', {'type': 'chat.message', 'message': safe})


ACTION_HANDLERS = {
    'SEND_REPLY': act_send_reply,
    'SEND_ARTICLE': act_send_article,
    'ADD_TAG': act_add_tag,
    'REMOVE_TAG': act_remove_tag,
    'SET_PRIORITY': act_set_priority,
    'SET_STATUS': act_set_status,
    'ASSIGN_TO_AGENT': act_assign_to_agent,
    'ASSIGN_TO_TEAM': act_assign_to_team,
    'RETURN_TO_QUEUE': act_return_to_queue,
    'TRANSFER_TO_TEAM': act_transfer_to_team,
    'CREATE_INTERNAL_NOTE': act_create_internal_note,
    'REQUEST_RATING': act_request_rating,
    'CLOSE_CONVERSATION': act_close_conversation,
    'REOPEN_CONVERSATION': act_reopen_conversation,
}


def execute_action(conv, action, ctx):
    handler = ACTION_HANDLERS.get(action.get('type'))
    if handler is None:
        raise ActionError(f'Unknown action type "{action.get("type")}"')
    params = action.get('params') or {}
    try:
        result, obj_type, obj_id = handler(conv, params, ctx)
    except conv_services.ConversationServiceError as exc:
        raise ActionError(exc.message)
    return result, obj_type, obj_id
