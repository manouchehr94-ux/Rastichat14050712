from rest_framework import viewsets, permissions
from .models import Project
from .serializers import ProjectSerializer
from common.permissions import IsWorkspaceAdmin

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsWorkspaceAdmin]

    def get_queryset(self):
        # Tenant Isolation: Filter by workspace memberships
        return Project.objects.filter(workspace__memberships__user=self.request.user)

    def perform_create(self, serializer):
        # Assign to the first workspace the user owns/admins
        workspace = self.request.user.workspace_memberships.first().workspace
        serializer.save(workspace=workspace)
