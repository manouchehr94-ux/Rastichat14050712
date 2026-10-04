from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets, status
from rest_framework.response import Response

from audit.models import AuditEvent
from common.mixins import WorkspaceScopedQuerysetMixin
from common.pagination import StandardPagination
from common.permissions import IsWorkspaceAdminOfObject, IsWorkspaceOperator, require_workspace_admin
from common.tenancy import resolve_operator_workspace
from teams.models import Team
from .models import Queue
from .serializers import QueueSerializer


class QueueViewSet(WorkspaceScopedQuerysetMixin, viewsets.ModelViewSet):
    """Tenant-scoped queues. Any workspace operator can list/view them (for
    routing/claim/transfer UI); only a Workspace Owner/Admin can manage them.
    """
    serializer_class = QueueSerializer
    pagination_class = StandardPagination
    queryset = Queue.objects.all()

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsWorkspaceOperator()]
        return [IsWorkspaceAdminOfObject()]

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        try:
            team = Team.objects.get(id=request.data.get('team'), workspace=workspace)
        except (Team.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            return Response({'error': 'Team not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        queue = serializer.save(workspace=workspace, team=team)
        AuditEvent.objects.create(
            actor=request.user, action='queue_created', target_type='queue', target_id=str(queue.id),
            metadata={'workspace_id': str(workspace.id), 'name': queue.name},
        )
        return Response(self.get_serializer(queue).data, status=status.HTTP_201_CREATED)
