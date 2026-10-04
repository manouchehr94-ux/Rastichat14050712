from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from audit.models import AuditEvent
from common.mixins import WorkspaceScopedQuerysetMixin
from common.pagination import StandardPagination
from common.permissions import IsWorkspaceAdminOfObject, IsWorkspaceOperator, require_workspace_admin
from common.tenancy import resolve_operator_workspace
from .models import Team, TeamMembership
from .serializers import TeamSerializer

User = get_user_model()


class TeamViewSet(WorkspaceScopedQuerysetMixin, viewsets.ModelViewSet):
    """Persistent, tenant-scoped support teams.

    Any workspace operator can list/view teams (needed for filters and the
    transfer/claim UI); only a Workspace Owner/Admin can create, edit,
    (de)activate a team, or manage its membership.
    """
    serializer_class = TeamSerializer
    pagination_class = StandardPagination
    queryset = Team.objects.all()

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsWorkspaceOperator()]
        return [IsWorkspaceAdminOfObject()]

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        team = serializer.save(workspace=workspace)
        AuditEvent.objects.create(
            actor=request.user, action='team_created', target_type='team', target_id=str(team.id),
            metadata={'workspace_id': str(workspace.id), 'name': team.name},
        )
        return Response(self.get_serializer(team).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save()
        AuditEvent.objects.create(
            actor=self.request.user, action='team_updated', target_type='team', target_id=str(serializer.instance.id),
        )

    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        team = self.get_object()
        user_id = request.data.get('user_id')
        try:
            member = User.objects.get(id=user_id, workspace_memberships__workspace=team.workspace)
        except (User.DoesNotExist, DjangoValidationError, ValueError):
            return Response({'error': 'User not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        role = request.data.get('role') or TeamMembership.Role.MEMBER
        if role not in TeamMembership.Role.values:
            return Response({'error': 'Invalid role'}, status=status.HTTP_400_BAD_REQUEST)
        membership, _ = TeamMembership.objects.update_or_create(
            team=team, user=member, defaults={'role': role, 'is_active': True},
        )
        AuditEvent.objects.create(
            actor=request.user, action='team_member_added', target_type='team', target_id=str(team.id),
            metadata={'user_id': str(member.id), 'role': role},
        )
        return Response(TeamSerializer(team).data)

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        team = self.get_object()
        user_id = request.data.get('user_id')
        TeamMembership.objects.filter(team=team, user_id=user_id).delete()
        AuditEvent.objects.create(
            actor=request.user, action='team_member_removed', target_type='team', target_id=str(team.id),
            metadata={'user_id': str(user_id)},
        )
        return Response(TeamSerializer(team).data)
