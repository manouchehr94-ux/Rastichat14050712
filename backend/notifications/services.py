from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from .models import Notification


def notify(recipient, workspace, event_type, title, payload=None):
    """Create a persistent notification and push it over the recipient's
    personal WebSocket group. Safe to call even with no channel layer
    configured (e.g. some test contexts) — the DB row is still created.
    """
    notif = Notification.objects.create(
        recipient=recipient, workspace=workspace, event_type=event_type, title=title, payload=payload or {},
    )
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(f'notifications_{recipient.id}', {
            'type': 'notification.created',
            'notification': {
                'id': str(notif.id), 'event_type': notif.event_type, 'title': notif.title,
                'payload': notif.payload, 'created_at': notif.created_at.isoformat(),
            },
        })
    return notif


def broadcast_presence_updated(user, workspace_ids, status):
    layer = get_channel_layer()
    if not layer:
        return
    for workspace_id in workspace_ids:
        async_to_sync(layer.group_send)(f'workspace_presence_{workspace_id}', {
            'type': 'agent.presence_updated',
            'user_id': str(user.id), 'status': status,
        })
