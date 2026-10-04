"""Who may open which live conversation — the single source of truth.

Used by the WebSocket consumers (connect + live re-validation) AND by the
ticket-issuing endpoints, so a ticket is only ever minted for a socket the
consumer would have accepted anyway. All functions are synchronous (ORM).
"""
from django.core.exceptions import ValidationError

from platforms.models import PlatformMembership
from workspaces.models import WorkspaceMembership

from .models import Conversation


def operator_conversation(user, conv_id):
    """Customer conversation `conv_id` iff `user` is an active member of its (active) workspace."""
    if user is None or not user.is_active:
        return None
    try:
        return Conversation.objects.get(
            id=conv_id, workspace__memberships__user=user, workspace__is_active=True,
            type=Conversation.Type.CUSTOMER,
        )
    except (Conversation.DoesNotExist, ValueError, ValidationError):
        return None


def support_conversation(user, conv_id):
    """Support conversation iff `user` is Owner/Admin of THIS workspace or platform staff of THIS
    workspace's platform, and the workspace and platform are active."""
    if user is None or not user.is_active:
        return None
    try:
        conv = Conversation.objects.select_related('workspace__platform').filter(
            id=conv_id, type=Conversation.Type.PLATFORM_SUPPORT,
        ).first()
    except (ValueError, ValidationError):
        return None
    if not conv or not conv.workspace.is_active or not conv.workspace.platform.is_active:
        return None
    is_ws_admin = WorkspaceMembership.objects.filter(
        user=user, workspace=conv.workspace, role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']).exists()
    is_pl_support = PlatformMembership.objects.filter(
        user=user, platform=conv.workspace.platform,
        role__in=['PLATFORM_OWNER', 'PLATFORM_ADMIN', 'PLATFORM_SUPPORT_AGENT']).exists()
    return conv if (is_ws_admin or is_pl_support) else None


def visitor_conversation(session, conv_id):
    """Customer conversation `conv_id` iff it belongs to the (already validated) session's visitor."""
    if session is None:
        return None
    try:
        return Conversation.objects.get(id=conv_id, visitor=session.visitor, type=Conversation.Type.CUSTOMER)
    except (Conversation.DoesNotExist, ValueError, ValidationError):
        return None
