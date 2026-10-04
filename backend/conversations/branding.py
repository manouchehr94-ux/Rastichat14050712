"""Builds the real store/consultant identity payload shown in the widget header.

Never invents data: every field is either a real value from the database or an
explicit null/empty fallback the widget is expected to render generically.
A specific consultant identity is only ever shown once a conversation has a
real assigned operator — before assignment, only real, workspace-level
aggregate data (e.g. "is anyone online right now") is exposed, never a
fabricated "sample" person.
"""
from accounts.models import OperatorPresence
from conversations.models import Message


def _operator_display_name(user):
    return user.display_name or user.email.split('@')[0]


def _response_time_label(operator, workspace):
    """Average minutes between a visitor message and the operator's next reply,
    over the operator's last 20 replies in this workspace. Returns None (not a
    fabricated default) when there isn't enough data yet.
    """
    replies = list(
        Message.objects.filter(
            conversation__workspace=workspace, sender=operator, sender_type=Message.SenderType.USER,
        ).order_by('-created_at')[:20]
    )
    if not replies:
        return None
    deltas = []
    for reply in replies:
        prior_visitor_msg = (
            Message.objects.filter(
                conversation=reply.conversation, sender_type=Message.SenderType.VISITOR,
                created_at__lt=reply.created_at,
            ).order_by('-created_at').first()
        )
        if prior_visitor_msg:
            deltas.append((reply.created_at - prior_visitor_msg.created_at).total_seconds())
    if not deltas:
        return None
    avg_minutes = (sum(deltas) / len(deltas)) / 60
    if avg_minutes < 1:
        return 'میانگین پاسخ کمتر از ۱ دقیقه'
    return f'میانگین پاسخ حدود {round(avg_minutes)} دقیقه'


def _operator_rating(operator):
    from django.db.models import Avg
    from conversations.models import Conversation
    avg = Conversation.objects.filter(assigned_to=operator, rating__isnull=False).aggregate(avg=Avg('rating'))['avg']
    return round(avg, 1) if avg is not None else None


def build_widget_branding(conversation):
    workspace = conversation.workspace
    project = conversation.visitor.project if conversation.visitor else None

    store = {
        'name': (project.name if project else workspace.name),
        'logo_url': (project.logo_url if project else '') or '',
        'subtitle': (project.subtitle if project else '') or '',
    }

    consultant = None
    operator = conversation.assigned_to
    if operator:
        presence = OperatorPresence.objects.filter(user=operator).first()
        consultant = {
            'display_name': _operator_display_name(operator),
            'avatar_url': operator.avatar_url or '',
            'title': operator.title or '',
            'status': presence.effective_status() if presence else OperatorPresence.Status.OFFLINE,
            'response_time_label': _response_time_label(operator, workspace),
            'rating': _operator_rating(operator),
        }

    workspace_online = OperatorPresence.objects.filter(
        user__workspace_memberships__workspace=workspace,
    ).exists() and any(
        p.effective_status() == OperatorPresence.Status.ONLINE
        for p in OperatorPresence.objects.filter(user__workspace_memberships__workspace=workspace)
    )

    return {'store': store, 'consultant': consultant, 'workspace_online': workspace_online}
