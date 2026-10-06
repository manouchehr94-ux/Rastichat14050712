from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from audit.models import AuditEvent
from common.permissions import require_workspace_admin
from common.throttles import StaffWriteThrottle
from visitors.sessions import enforce_project_origin

from .models import Project, ProjectWidgetConfig
from .widget_config import ConfigError, public_config, resolve_config, validate_config


class WidgetConfigView(APIView):
    """`GET /api/v1/widget/config/?project_key=<public key>` — public, versioned widget configuration.

    The project key is a public identifier (not a secret). The project's allowed-domain policy applies when an
    `Origin` is present (CORS for this prefix follows the configured domains); nothing here is sensitive: it is what
    the widget would render anyway. Unknown / inactive projects are a uniform 404.
    """
    permission_classes = []
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'widget_config'

    def get(self, request):
        key = request.query_params.get('project_key')
        try:
            project = Project.objects.select_related('workspace', 'widget_config').filter(
                public_key=key, is_active=True, workspace__is_active=True).first()
        except Exception:  # noqa: BLE001 - malformed uuid: same answer as unknown
            project = None
        if project is None:
            return Response({'error': 'Unknown project.', 'code': 'invalid_project'}, status=status.HTTP_404_NOT_FOUND)
        enforce_project_origin(request, project, establishing=False)
        response = Response(public_config(project))
        response['Cache-Control'] = 'public, max-age=30'
        return response


class ProjectWidgetConfigAdminView(APIView):
    """`GET|PUT /api/v1/projects/<id>/widget-config/` — for Owner/Admin of THIS project's workspace only (an admin role
    in another workspace never counts). PUT replaces the stored document after strict validation."""

    def get_throttles(self):
        return [StaffWriteThrottle()] if self.request.method == 'PUT' else super().get_throttles()

    @staticmethod
    def _project(request, pk):
        # the project is looked up only among workspaces the caller belongs to; admin-ness is then checked for THAT workspace
        project = get_object_or_404(
            Project.objects.select_related('workspace').filter(workspace__memberships__user=request.user), pk=pk)
        require_workspace_admin(request.user, project.workspace)
        return project

    def get(self, request, pk):
        project = self._project(request, pk)
        stored = getattr(getattr(project, 'widget_config', None), 'config', {}) or {}
        return Response({'config': stored, 'effective': resolve_config(project)})

    def put(self, request, pk):
        project = self._project(request, pk)
        try:
            clean = validate_config(request.data)
        except ConfigError as exc:
            return Response({'error': 'Invalid widget configuration.', 'code': 'invalid_config', 'errors': exc.errors},
                            status=status.HTTP_400_BAD_REQUEST)
        row, _ = ProjectWidgetConfig.objects.update_or_create(
            project=project, defaults={'config': clean, 'updated_by': request.user})
        AuditEvent.objects.create(actor=request.user, action='widget_config_updated', target_type='project',
                                  target_id=str(project.pk), metadata={'sections': sorted(k for k in clean if k != 'version')})
        return Response({'config': row.config, 'effective': resolve_config(project)})


class AdminProjectListView(APIView):
    """`GET /api/v1/projects/` — the projects of workspaces where the caller is Owner/Admin of THAT workspace (used by
    the widget settings screen and to show the embed snippet; the project key is a public identifier)."""

    def get(self, request):
        from common.tenancy import admin_workspace_ids
        projects = Project.objects.filter(workspace_id__in=admin_workspace_ids(request.user)).order_by('workspace_id', 'id')
        return Response([{
            'id': p.pk, 'name': p.name, 'workspace_id': p.workspace_id, 'public_key': str(p.public_key),
            'is_active': p.is_active, 'allowed_domains': p.allowed_domains,
        } for p in projects])
