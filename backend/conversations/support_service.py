"""Tenant <-> platform support conversations — one service for BOTH directions.

    tenant admin -> platform support   (a tenant opens/resumes a thread)
    platform staff -> tenant admins    (the platform opens/resumes a thread; the tenant never has to write first)

Generic platform/tenant semantics: nothing here knows what a "store" is. "Platform" = the operator of the workspace's
`Platform`; "tenant" = the workspace. The same conversation engine (messages, receipts, WebSocket group
`support_chat_<id>`) carries both directions.

Rules (each tested): at most ONE active thread per (workspace, subject_key) — start is idempotent and resumes; every message is
idempotent on `client_message_id`; the other side is notified; status says who owes the next answer; a closed thread is
never silently reused (a new start opens a fresh one) but a reply to it reopens it; history is never deleted.
"""
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import IntegrityError, transaction

from audit.models import AuditEvent
from notifications.models import Notification
from notifications.services import notify
from platforms.models import PlatformMembership
from workspaces.models import WorkspaceMembership

from common import observability
from .models import Conversation, Message

Side = Conversation.Side
Status = Conversation.Status
MAX_MESSAGE = 5000
PLATFORM_INITIATOR_ROLES = ('PLATFORM_OWNER', 'PLATFORM_ADMIN')   # who may OPEN a thread to a tenant (any platform staff may reply)
TENANT_ROLES = ('WORKSPACE_OWNER', 'WORKSPACE_ADMIN')


class SupportError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def platform_staff_ids(workspace):
    return list(PlatformMembership.objects.filter(platform=workspace.platform).values_list('user_id', flat=True))


def tenant_admin_ids(workspace):
    return list(WorkspaceMembership.objects.filter(workspace=workspace, role__in=TENANT_ROLES).values_list('user_id', flat=True))


def side_of(user, conv):
    """Which side `user` speaks for on `conv`: platform staff of the workspace's platform first, else a tenant admin of it."""
    if PlatformMembership.objects.filter(user=user, platform=conv.workspace.platform).exists():
        return Side.PLATFORM
    if WorkspaceMembership.objects.filter(user=user, workspace=conv.workspace, role__in=TENANT_ROLES).exists():
        return Side.TENANT
    return None


def _awaiting(side):
    """The status after `side` speaks: the OTHER side now owes an answer."""
    return Status.WAITING_FOR_WORKSPACE if side == Side.PLATFORM else Status.WAITING_FOR_PLATFORM


def realtime_payload(msg, side):
    """The frame sent over `support_chat_<id>`: `sender_side` ('platform'|'tenant') lets either UI render "mine" vs "theirs"
    even though both sides' messages are `sender_type: USER`."""
    return {'id': str(msg.id), 'sender_type': 'USER', 'sender_id': msg.sender_id, 'sender_side': (side or '').lower(),
            'content': msg.content, 'created_at': msg.created_at.isoformat(), 'client_message_id': msg.client_message_id}


def serialize_messages(conv, messages, serializer_class, context=None):
    """Message list for a support thread, each annotated with `sender_id` / `sender_side`."""
    platform_ids = set(platform_staff_ids(conv.workspace))
    data = serializer_class(messages, many=True, context=context or {}).data
    by_id = {str(m.id): m for m in messages}
    for row in data:
        sender_id = by_id[str(row['id'])].sender_id
        row['sender_id'] = sender_id
        row['sender_side'] = 'platform' if sender_id in platform_ids else 'tenant'
    return data


def _broadcast(conv, msg, side):
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(f'support_chat_{conv.id}', {'type': 'chat.message', 'message': realtime_payload(msg, side)})


def _notify_other_side(conv, sender, side):
    if side == Side.PLATFORM:
        recipients = tenant_admin_ids(conv.workspace)
        title = 'پیام جدید از پشتیبانی پلتفرم'
    else:
        recipients = platform_staff_ids(conv.workspace)
        title = f'پیام جدید از «{conv.workspace.name}»'
    from django.contrib.auth import get_user_model
    for user in get_user_model().objects.filter(pk__in=recipients, is_active=True).exclude(pk=sender.pk):
        notify(user, conv.workspace, Notification.EventType.SUPPORT_MESSAGE, title,
               {'conversation_id': str(conv.id), 'workspace_id': conv.workspace_id, 'from': side.lower()})


def _clean(content):
    content = (content or '').strip()
    if not content:
        raise SupportError('empty_message', 'Message is empty.')
    if len(content) > MAX_MESSAGE:
        raise SupportError('message_too_long', f'Message is longer than {MAX_MESSAGE} characters.')
    return content


@transaction.atomic
def post_message(conv, user, side, content, client_message_id, broadcast=True):
    """Append a message from `side`. Idempotent on `client_message_id`: returns (message, created)."""
    content = _clean(content)
    if not client_message_id or len(str(client_message_id)) > 255:
        raise SupportError('missing_client_message_id', 'client_message_id is required.')
    conv = Conversation.objects.select_for_update().get(pk=conv.pk)
    existing = Message.objects.filter(conversation=conv, client_message_id=client_message_id).first()
    if existing is not None:
        return existing, False
    msg = Message.objects.create(conversation=conv, sender=user, sender_type=Message.SenderType.USER, content=content,
                                 client_message_id=client_message_id)
    reopened = conv.status in (Status.CLOSED, Status.RESOLVED)
    conv.status = _awaiting(side)
    if reopened:
        conv.closed_at = None
        conv.resolved_at = None
    conv.save()
    if reopened:
        AuditEvent.objects.create(actor=user, action='support_conversation_reopened', target_type='conversation',
                                  target_id=str(conv.id), metadata={'by': side.lower(), 'reason': 'message'})
    transaction.on_commit(lambda: ((_broadcast(conv, msg, side) if broadcast else None), _notify_other_side(conv, user, side)))
    return msg, True


def start_thread(workspace, user, side, *, subject, subject_key, message, client_message_id):
    """Start the active thread for (workspace, subject_key) or resume it. Returns (conversation, created, message).

    `subject_key` makes this idempotent: concurrent/retried starts converge on ONE active conversation (a partial unique
    constraint decides the race). A resume appends the message to the existing thread."""
    subject_key = (subject_key or 'general').strip()[:64]
    subject = (subject or 'Support').strip()[:255]
    _clean(message)
    active = dict(workspace=workspace, type=Conversation.Type.PLATFORM_SUPPORT, subject_key=subject_key,
                  status__in=Conversation.ACTIVE_STATUSES)
    created = False
    conv = Conversation.objects.filter(**active).first()
    if conv is None:
        try:
            with transaction.atomic():
                conv = Conversation.objects.create(
                    workspace=workspace, type=Conversation.Type.PLATFORM_SUPPORT, subject=subject, subject_key=subject_key,
                    status=_awaiting(side), opened_by_side=side)
                AuditEvent.objects.create(
                    actor=user, action='support_conversation_created', target_type='conversation', target_id=str(conv.id),
                    metadata={'opened_by': side.lower(), 'workspace_id': workspace.pk, 'subject_key': subject_key})
            created = True
        except IntegrityError:
            conv = Conversation.objects.filter(**active).first()   # lost the race: resume the winner's thread
            if conv is None:
                raise
    # without a client_message_id every call is a new message; pass one (or an Idempotency-Key at the integration API) for retry safety
    msg, _ = post_message(conv, user, side, message, client_message_id or f'start_{uuid.uuid4().hex}')
    conv.refresh_from_db()
    observability.emit('support_thread', label='started' if created else 'resumed', side=side)
    return conv, created, msg


@transaction.atomic
def close_thread(conv, user, side):
    conv = Conversation.objects.select_for_update().get(pk=conv.pk)
    if conv.status == Status.CLOSED:
        return conv, False
    from django.utils import timezone
    conv.status = Status.CLOSED
    conv.closed_at = timezone.now()
    conv.save()
    AuditEvent.objects.create(actor=user, action='support_conversation_closed', target_type='conversation',
                              target_id=str(conv.id), metadata={'by': side.lower()})
    return conv, True


@transaction.atomic
def reopen_thread(conv, user, side):
    conv = Conversation.objects.select_for_update().get(pk=conv.pk)
    if conv.status not in (Status.CLOSED, Status.RESOLVED):
        return conv, False
    clash = Conversation.objects.filter(workspace=conv.workspace, type=Conversation.Type.PLATFORM_SUPPORT,
                                        subject_key=conv.subject_key, status__in=Conversation.ACTIVE_STATUSES
                                        ).exclude(pk=conv.pk).exists() if conv.subject_key else False
    if clash:
        raise SupportError('thread_already_active', 'Another thread on the same subject is already open.', 409)
    conv.status = _awaiting(side)
    conv.closed_at = None
    conv.resolved_at = None
    conv.save()
    AuditEvent.objects.create(actor=user, action='support_conversation_reopened', target_type='conversation',
                              target_id=str(conv.id), metadata={'by': side.lower(), 'reason': 'manual'})
    return conv, True


def mark_read(conv, user):
    from .models import MessageReceipt
    # receipts for every message the user has not yet 'seen' (own messages included — kept from the original endpoint)
    for msg in conv.messages.exclude(receipts__user=user):
        MessageReceipt.objects.create(message=msg, user=user)
