"""Action execution. Every handler is a small, hardcoded Python function
keyed by the validated action_type string — never a dynamically-constructed
call. Handlers use the SAME approved domain services the rest of the
product uses (conversations.services, queues.services, sla.services,
notifications.services, collaboration.services) rather than mutating models
directly, per spec section 4. The two exceptions (tag add/remove) are a
single get_or_create/delete against a pure join table with no business
logic to duplicate — see customer_context.views for the equivalent
operator-facing code this mirrors.

Every handler returns a small JSON-safe dict (never a model instance, never
a stack trace) — this is exactly what gets persisted into
AutomationActionExecution.result_summary.
"""
from django.conf import settings
from django.utils import timezone

from conversations import services as conv_services
from conversations.models import Conversation
from notifications.models import Notification
from notifications.services import notify

from .idempotency import run_idempotent

# Conservative default cooldown for SEND_CUSTOMER_MESSAGE: bounds a
# misconfigured or looping rule from flooding a visitor, without affecting
# any normal single-fire (or even a handful of distinct rules firing on the
# same conversation) usage. Override via Django settings if a workspace
# genuinely needs a different ceiling.
AUTOMATION_CUSTOMER_MESSAGE_RATE_LIMIT = getattr(settings, 'AUTOMATION_CUSTOMER_MESSAGE_RATE_LIMIT', 5)
AUTOMATION_CUSTOMER_MESSAGE_RATE_WINDOW_SECONDS = getattr(settings, 'AUTOMATION_CUSTOMER_MESSAGE_RATE_WINDOW_SECONDS', 300)


class ActionError(Exception):
    pass


def _resolve_agent(workspace, agent_id):
    user = workspace.memberships.filter(user_id=agent_id).select_related('user').first()
    if not user:
        raise ActionError('Agent is not a member of this workspace')
    return user.user


def _resolve_team(workspace, team_id):
    from teams.models import Team
    team = Team.objects.filter(id=team_id, workspace=workspace).first()
    if not team:
        raise ActionError('Team not found in this workspace')
    return team


def _resolve_queue(workspace, queue_id):
    from queues.models import Queue
    queue = Queue.objects.filter(id=queue_id, workspace=workspace).first()
    if not queue:
        raise ActionError('Queue not found in this workspace')
    return queue


def _resolve_tag(workspace, tag_id):
    from customer_context.models import Tag
    tag = Tag.objects.filter(id=tag_id, workspace=workspace).first()
    if not tag:
        raise ActionError('Tag not found in this workspace')
    return tag


def _idempotency_key(ctx, conv, action_type, extra=''):
    """Deterministic, retry-stable key for run_idempotent(): built from
    ctx.retry_seed (stable across retries of the SAME logical unit of work —
    a ScheduledAction's own id for the scheduled path, never a per-attempt
    AutomationExecution id), the rule, the conversation, the action type,
    and the action's position — plus an optional per-recipient suffix for
    multi-recipient actions. Two DIFFERENT scheduled/instant firings (e.g.
    two separate rule matches, or two distinct SCHEDULE_ACTION hops) always
    get different retry_seeds, so legitimate repeats are never suppressed —
    only a genuine retry of the identical attempt reuses this key.
    """
    key = f'{ctx.retry_seed}:{ctx.rule_id or ""}:{conv.id}:{action_type}:{ctx.action_index}'
    return f'{key}:{extra}' if extra else key


def act_assign_to_agent(conv, params, ctx):
    agent = _resolve_agent(conv.workspace, params['agent_id'])

    def _do():
        conv_services.assign_to_agent(conv, actor=None, target_user_id=agent.id, reason='Automation')
        return {'assigned_to': str(agent.id)}, 'user', str(agent.id)

    key = _idempotency_key(ctx, conv, 'ASSIGN_TO_AGENT')
    return run_idempotent(key, 'ASSIGN_TO_AGENT', conv.workspace_id, conv, _do)


def act_assign_to_team(conv, params, ctx):
    team = _resolve_team(conv.workspace, params['team_id'])

    def _do():
        conv_services.transfer_team(conv, actor=None, new_team_id=team.id, reason='Automation')
        return {'team_id': str(team.id)}, 'team', str(team.id)

    key = _idempotency_key(ctx, conv, 'ASSIGN_TO_TEAM')
    return run_idempotent(key, 'ASSIGN_TO_TEAM', conv.workspace_id, conv, _do)


def act_assign_using_queue_strategy(conv, params, ctx):
    from queues.services import auto_assign
    queue = _resolve_queue(conv.workspace, params['queue_id'])

    def _do():
        updated = auto_assign(conv, queue)
        return {'assigned_to': str(updated.assigned_to_id) if updated.assigned_to_id else None}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'ASSIGN_USING_QUEUE_STRATEGY')
    return run_idempotent(key, 'ASSIGN_USING_QUEUE_STRATEGY', conv.workspace_id, conv, _do)


def act_unassign(conv, params, ctx):
    def _do():
        conv_services.unassign(conv, actor=None, reason='Automation')
        return {}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'UNASSIGN')
    return run_idempotent(key, 'UNASSIGN', conv.workspace_id, conv, _do)


def act_return_to_queue(conv, params, ctx):
    def _do():
        conv_services.return_to_queue(conv, actor=None, reason='Automation')
        return {}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'RETURN_TO_QUEUE')
    return run_idempotent(key, 'RETURN_TO_QUEUE', conv.workspace_id, conv, _do)


def act_transfer_to_team(conv, params, ctx):
    team = _resolve_team(conv.workspace, params['team_id'])

    def _do():
        conv_services.transfer_team(conv, actor=None, new_team_id=team.id, reason=params.get('reason', 'Automation'))
        return {'team_id': str(team.id)}, 'team', str(team.id)

    key = _idempotency_key(ctx, conv, 'TRANSFER_TO_TEAM')
    return run_idempotent(key, 'TRANSFER_TO_TEAM', conv.workspace_id, conv, _do)


def act_escalate(conv, params, ctx):
    def _do():
        conv_services.escalate(conv, actor=None, reason=params.get('reason', 'Automation'))
        return {}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'ESCALATE')
    return run_idempotent(key, 'ESCALATE', conv.workspace_id, conv, _do)


def act_set_priority(conv, params, ctx):
    from conversations.models import PriorityChange

    def _do():
        conv_services.set_priority(
            conv, actor=None, priority=params['priority'], reason='Automation', reason_code=PriorityChange.Reason.MANUAL,
        )
        return {'priority': params['priority']}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'SET_PRIORITY')
    return run_idempotent(key, 'SET_PRIORITY', conv.workspace_id, conv, _do)


def act_set_status(conv, params, ctx):
    target = params['status']

    def _do():
        if target == Conversation.Status.CLOSED:
            conv_services.close_conversation(conv, actor=None)
        elif target == Conversation.Status.OPEN and conv.status == Conversation.Status.CLOSED:
            conv_services.reopen_conversation(conv, actor=None)
        else:
            # Every other target status routes through the shared set_status()
            # service (audit event, CONVERSATION_STATUS_CHANGED publish, ops
            # broadcast) instead of a raw queryset .update() that silently
            # bypassed all three.
            conv_services.set_status(conv, actor=None, status=target, reason='Automation')
        return {'status': target}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'SET_STATUS')
    return run_idempotent(key, 'SET_STATUS', conv.workspace_id, conv, _do)


def act_add_tag(conv, params, ctx):
    from customer_context.models import ConversationTag
    tag = _resolve_tag(conv.workspace, params['tag_id'])

    def _do():
        ConversationTag.objects.get_or_create(conversation=conv, tag=tag, defaults={'created_by': None})
        return {'tag_id': str(tag.id)}, 'tag', str(tag.id)

    key = _idempotency_key(ctx, conv, 'ADD_TAG', extra=str(tag.id))
    return run_idempotent(key, 'ADD_TAG', conv.workspace_id, conv, _do)


def act_remove_tag(conv, params, ctx):
    from customer_context.models import ConversationTag
    tag = _resolve_tag(conv.workspace, params['tag_id'])

    def _do():
        ConversationTag.objects.filter(conversation=conv, tag=tag).delete()
        return {'tag_id': str(tag.id)}, 'tag', str(tag.id)

    key = _idempotency_key(ctx, conv, 'REMOVE_TAG', extra=str(tag.id))
    return run_idempotent(key, 'REMOVE_TAG', conv.workspace_id, conv, _do)


def act_send_customer_message(conv, params, ctx):
    from .templating import resolve_template, TemplateError
    if not conv.visitor_id:
        raise ActionError('Conversation has no visitor to message')
    try:
        body = resolve_template(params['template'], conv)
    except TemplateError as exc:
        raise ActionError(str(exc))
    client_msg_id = f'automation:{ctx.retry_seed}:{ctx.action_index}'
    from conversations.models import Message
    if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
        return {'skipped': 'duplicate'}, 'message', ''
    _enforce_customer_message_rate_limit(conv)
    msg = Message.objects.create(
        conversation=conv, sender_type=Message.SenderType.SYSTEM, content=body,
        client_message_id=client_msg_id, message_type=Message.MessageType.TEXT,
    )
    conv_services.broadcast_ops_event(conv.id, 'conversation.automation_message', {'content': body})
    _broadcast_widget_message(conv, msg)
    return {'message_id': str(msg.id)}, 'message', str(msg.id)


def _enforce_customer_message_rate_limit(conv):
    """Bounds SEND_CUSTOMER_MESSAGE to a rolling per-conversation ceiling —
    the per-instance client_message_id dedup above only stops the exact same
    action-instance firing twice, not a misconfigured rule (or loop) sending
    many *distinct* automated messages to the same visitor in a short span.
    """
    from conversations.models import Message
    window_start = timezone.now() - timezone.timedelta(seconds=AUTOMATION_CUSTOMER_MESSAGE_RATE_WINDOW_SECONDS)
    recent = Message.objects.filter(
        conversation=conv, sender_type=Message.SenderType.SYSTEM, message_type=Message.MessageType.TEXT,
        created_at__gte=window_start,
    ).count()
    if recent >= AUTOMATION_CUSTOMER_MESSAGE_RATE_LIMIT:
        raise ActionError(
            f'Automated customer-message rate limit reached for this conversation '
            f'({AUTOMATION_CUSTOMER_MESSAGE_RATE_LIMIT} per {AUTOMATION_CUSTOMER_MESSAGE_RATE_WINDOW_SECONDS}s).'
        )


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


def act_create_internal_note(conv, params, ctx):
    from conversations.models import Message
    client_msg_id = f'automation-note:{ctx.retry_seed}:{ctx.action_index}'
    if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
        return {'skipped': 'duplicate'}, 'message', ''
    note = Message.objects.create(
        conversation=conv, sender_type=Message.SenderType.SYSTEM, content=params['content'][:4000],
        client_message_id=client_msg_id, message_type=Message.MessageType.INTERNAL_NOTE,
    )
    conv_services.broadcast_ops_event(conv.id, 'conversation.internal_note_created', {'note': {'content': note.content, 'automated': True}})
    return {'note_id': str(note.id)}, 'message', str(note.id)


def _notify_recipients_idempotently(conv, params, ctx, action_type, recipients):
    """Each recipient gets their OWN idempotency key (the action itself may
    fan out to several people), so a crash-point-B retry that already
    notified recipient A but crashed before reaching B does not re-notify
    A — A's key is already completed and returns its cached result, while B
    (and anyone after) still runs for real. A single recipient's failure
    does not roll back an earlier recipient's already-completed
    notification (run_idempotent only ever deletes/retries its OWN key).
    """
    notified = []
    for user in recipients:
        def _do(user=user):
            notify(user, conv.workspace, Notification.EventType.AUTOMATION_TRIGGERED, params['title'][:255], {'conversation_id': str(conv.id)})
            return {'notified': str(user.id)}, 'user', str(user.id)

        key = _idempotency_key(ctx, conv, action_type, extra=str(user.id))
        run_idempotent(key, action_type, conv.workspace_id, conv, _do)
        notified.append(str(user.id))
    return {'recipient_count': len(notified)}, 'conversation', str(conv.id)


def act_send_notification(conv, params, ctx):
    recipients = _notification_targets(conv, params.get('target', 'ASSIGNEE'))
    return _notify_recipients_idempotently(conv, params, ctx, 'SEND_NOTIFICATION', recipients)


def act_notify_supervisor(conv, params, ctx):
    recipients = _notification_targets(conv, 'TEAM_SUPERVISORS')
    return _notify_recipients_idempotently(conv, params, ctx, 'NOTIFY_SUPERVISOR', recipients)


def _notification_targets(conv, target):
    from teams.models import TeamMembership
    if target == 'ASSIGNEE':
        return [conv.assigned_to] if conv.assigned_to_id else []
    if target == 'TEAM_SUPERVISORS':
        if not conv.team_id:
            return []
        return [m.user for m in TeamMembership.objects.filter(team_id=conv.team_id, role='SUPERVISOR', is_active=True).select_related('user')]
    if target == 'WORKSPACE_ADMINS':
        return [m.user for m in conv.workspace.memberships.filter(role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']).select_related('user')]
    return []


def act_request_rating(conv, params, ctx):
    from conversations.models import Message
    client_msg_id = f'automation-rating:{ctx.retry_seed}:{ctx.action_index}'
    if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
        return {'skipped': 'duplicate'}, 'message', ''
    msg = Message.objects.create(
        conversation=conv, sender_type=Message.SenderType.SYSTEM, content='',
        client_message_id=client_msg_id, message_type=Message.MessageType.RATING_REQUEST,
    )
    _broadcast_widget_message(conv, msg)
    return {'message_id': str(msg.id)}, 'message', str(msg.id)


def act_close_conversation(conv, params, ctx):
    def _do():
        conv_services.close_conversation(conv, actor=None)
        return {}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'CLOSE_CONVERSATION')
    return run_idempotent(key, 'CLOSE_CONVERSATION', conv.workspace_id, conv, _do)


def act_reopen_conversation(conv, params, ctx):
    def _do():
        try:
            conv_services.reopen_conversation(conv, actor=None)
        except conv_services.ConversationServiceError:
            return {'skipped': 'not_closed'}, 'conversation', str(conv.id)
        return {}, 'conversation', str(conv.id)

    key = _idempotency_key(ctx, conv, 'REOPEN_CONVERSATION')
    return run_idempotent(key, 'REOPEN_CONVERSATION', conv.workspace_id, conv, _do)


def act_schedule_action(conv, params, ctx):
    from .scheduling import schedule_action
    delay = params['delay_minutes']
    execute_at = timezone.now() + timezone.timedelta(minutes=delay)
    scheduled = schedule_action(
        workspace=conv.workspace, rule_id=ctx.rule_id, conversation=conv, action_definition=params['action'],
        execute_at=execute_at, correlation_id=ctx.correlation_id,
        # The wrapped action is the NEXT hop in the chain, not a continuation
        # of this one — persisting ctx.depth (instead of ctx.depth + 1)
        # under-counted the chain by one hop, letting a scheduled-action hop
        # execute unchecked one level past where process_event's depth guard
        # would otherwise have stopped it.
        depth=ctx.depth + 1,
        idempotency_seed=f'{ctx.rule_id}:{conv.id}:{ctx.correlation_id}:{ctx.action_index}',
    )
    return {'scheduled_action_id': str(scheduled.id), 'execute_at': execute_at.isoformat()}, 'scheduled_action', str(scheduled.id)


def act_cancel_scheduled_action(conv, params, ctx):
    from .models import ScheduledAction
    qs = ScheduledAction.objects.filter(workspace=conv.workspace, conversation=conv, status=ScheduledAction.Status.PENDING)
    key = params.get('idempotency_key')
    if key:
        qs = qs.filter(idempotency_key=key)
    count = qs.update(status=ScheduledAction.Status.CANCELLED, cancelled_at=timezone.now())
    return {'cancelled_count': count}, 'conversation', str(conv.id)


ACTION_HANDLERS = {
    'ASSIGN_TO_AGENT': act_assign_to_agent,
    'ASSIGN_TO_TEAM': act_assign_to_team,
    'ASSIGN_USING_QUEUE_STRATEGY': act_assign_using_queue_strategy,
    'UNASSIGN': act_unassign,
    'RETURN_TO_QUEUE': act_return_to_queue,
    'TRANSFER_TO_TEAM': act_transfer_to_team,
    'ESCALATE': act_escalate,
    'SET_PRIORITY': act_set_priority,
    'SET_STATUS': act_set_status,
    'ADD_TAG': act_add_tag,
    'REMOVE_TAG': act_remove_tag,
    'SEND_CUSTOMER_MESSAGE': act_send_customer_message,
    'CREATE_INTERNAL_NOTE': act_create_internal_note,
    'SEND_NOTIFICATION': act_send_notification,
    'NOTIFY_SUPERVISOR': act_notify_supervisor,
    'REQUEST_RATING': act_request_rating,
    'CLOSE_CONVERSATION': act_close_conversation,
    'REOPEN_CONVERSATION': act_reopen_conversation,
    'SCHEDULE_ACTION': act_schedule_action,
    'CANCEL_SCHEDULED_ACTION': act_cancel_scheduled_action,
}


def execute_action(conv, action, ctx):
    """Run one action. Returns (result_summary, affected_type, affected_id).
    Raises ActionError (caught by the engine, recorded as FAILED) or lets a
    ConversationServiceError from the underlying domain service propagate
    the same way — both are safe-summary-only, never a raw traceback.
    """
    handler = ACTION_HANDLERS.get(action.get('type'))
    if handler is None:
        raise ActionError(f'Unknown action type "{action.get("type")}"')
    params = action.get('params') or {}
    try:
        result, obj_type, obj_id = handler(conv, params, ctx)
    except conv_services.ConversationServiceError as exc:
        raise ActionError(exc.message)
    return result, obj_type, obj_id
