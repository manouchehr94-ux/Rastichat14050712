from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.db.models import F
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from common.pagination import StandardPagination
from common.permissions import IsWorkspaceOperator, user_has_workspace_role
from common.tenancy import resolve_operator_workspace
from teams.models import Team, TeamMembership
from .models import QuickReply
from .serializers import QuickReplySerializer


class IsQuickReplyManager(permissions.BasePermission):
    """Object-level write authorization for an *existing* quick reply,
    independent of who can merely see it (see QuickReplyViewSet.get_queryset
    — visibility does not imply write permission). PERSONAL: only its owner.
    TEAM: a SUPERVISOR of that team, that team's manager, or a workspace
    admin — never just any team member, even though any active member may
    create one. WORKSPACE: only a workspace admin, matching the same
    requirement already enforced at creation time.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if obj.scope == QuickReply.Scope.PERSONAL:
            return obj.owner_id == user.id
        if obj.scope == QuickReply.Scope.WORKSPACE:
            return user_has_workspace_role(user, obj.workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN'])
        if obj.scope == QuickReply.Scope.TEAM:
            if user_has_workspace_role(user, obj.workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']):
                return True
            if obj.team_id and obj.team.manager_id == user.id:
                return True
            return TeamMembership.objects.filter(
                team_id=obj.team_id, user=user, role=TeamMembership.Role.SUPERVISOR, is_active=True,
            ).exists()
        return False


class QuickReplyViewSet(viewsets.ModelViewSet):
    """Personal / team / workspace managed quick replies.

    Visibility is role-aware: a PERSONAL reply is only ever visible to its
    owner; a TEAM reply only to active members of that team; a WORKSPACE
    reply to every workspace member. Creating a TEAM or WORKSPACE reply
    requires actually belonging to that team / being a workspace admin —
    an operator can never plant a reply somewhere they have no standing.
    """
    serializer_class = QuickReplySerializer
    permission_classes = [IsWorkspaceOperator]
    pagination_class = StandardPagination

    def get_permissions(self):
        if self.action in ('update', 'partial_update', 'destroy'):
            return [IsWorkspaceOperator(), IsQuickReplyManager()]
        return [IsWorkspaceOperator()]

    def get_queryset(self):
        user = self.request.user
        team_ids = TeamMembership.objects.filter(user=user, is_active=True).values_list('team_id', flat=True)
        qs = QuickReply.objects.filter(workspace__memberships__user=user, is_active=True).filter(
            models.Q(scope=QuickReply.Scope.WORKSPACE)
            | models.Q(scope=QuickReply.Scope.PERSONAL, owner=user)
            | models.Q(scope=QuickReply.Scope.TEAM, team_id__in=team_ids)
        ).distinct()
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                models.Q(title__icontains=search) | models.Q(body__icontains=search) | models.Q(shortcut__icontains=search)
            )
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        return qs

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        scope = request.data.get('scope', QuickReply.Scope.PERSONAL)
        owner = None
        team = None
        if scope == QuickReply.Scope.PERSONAL:
            owner = request.user
        elif scope == QuickReply.Scope.TEAM:
            try:
                team = Team.objects.get(id=request.data.get('team'), workspace=workspace)
            except (Team.DoesNotExist, DjangoValidationError, ValueError, TypeError):
                return Response({'error': 'Team not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
            if not TeamMembership.objects.filter(team=team, user=request.user, is_active=True).exists():
                return Response({'error': 'Not a member of that team'}, status=status.HTTP_403_FORBIDDEN)
        elif scope == QuickReply.Scope.WORKSPACE:
            if not request.user.workspace_memberships.filter(
                workspace=workspace, role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN'],
            ).exists():
                return Response({'error': 'Only a workspace admin can create a workspace-wide quick reply'}, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({'error': 'Invalid scope'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply = serializer.save(workspace=workspace, owner=owner, team=team, created_by=request.user, updated_by=request.user)
        return Response(self.get_serializer(reply).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=True, methods=['post'])
    def use(self, request, pk=None):
        """Increment usage_count and return the reply body with the
        supported template variables resolved for the given conversation.
        """
        from conversations.models import Conversation
        from .services import build_variable_context, resolve_quick_reply

        reply = self.get_object()
        QuickReply.objects.filter(pk=reply.pk).update(usage_count=F('usage_count') + 1)
        reply.refresh_from_db(fields=['usage_count'])

        conv_id = request.data.get('conversation_id')
        body = reply.body
        if conv_id:
            try:
                conv = Conversation.objects.get(id=conv_id, workspace__memberships__user=request.user)
                body = resolve_quick_reply(reply.body, build_variable_context(conv, request.user))
            except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
                pass
        return Response({'body': body, 'usage_count': reply.usage_count})
