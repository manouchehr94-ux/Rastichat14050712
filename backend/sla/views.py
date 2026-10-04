from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from common.mixins import WorkspaceScopedQuerysetMixin
from common.pagination import StandardPagination
from common.permissions import IsWorkspaceAdminOfObject, IsWorkspaceOperator, require_workspace_admin
from common.tenancy import resolve_operator_workspace
from conversations.models import Conversation
from .models import BusinessCalendar, WorkingInterval, Holiday, SLAPolicy
from .serializers import (
    BusinessCalendarSerializer, WorkingIntervalSerializer, HolidaySerializer, SLAPolicySerializer,
    ConversationSLASerializer,
)


class BusinessCalendarViewSet(WorkspaceScopedQuerysetMixin, viewsets.ModelViewSet):
    serializer_class = BusinessCalendarSerializer
    pagination_class = StandardPagination
    queryset = BusinessCalendar.objects.all()

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsWorkspaceOperator()]
        return [IsWorkspaceAdminOfObject()]

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        calendar = serializer.save(workspace=workspace)
        return Response(self.get_serializer(calendar).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def add_interval(self, request, pk=None):
        calendar = self.get_object()
        serializer = WorkingIntervalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(calendar=calendar)
        return Response(self.get_serializer(calendar).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def add_holiday(self, request, pk=None):
        calendar = self.get_object()
        serializer = HolidaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(calendar=calendar)
        return Response(self.get_serializer(calendar).data, status=status.HTTP_201_CREATED)


class SLAPolicyViewSet(WorkspaceScopedQuerysetMixin, viewsets.ModelViewSet):
    serializer_class = SLAPolicySerializer
    pagination_class = StandardPagination
    queryset = SLAPolicy.objects.all()

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsWorkspaceOperator()]
        return [IsWorkspaceAdminOfObject()]

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        policy = serializer.save(workspace=workspace)
        return Response(self.get_serializer(policy).data, status=status.HTTP_201_CREATED)


class ConversationSLAView(APIView):
    """Read-only SLA detail for one conversation (the inbox already embeds a
    summary; this is the fuller view for a dedicated SLA panel).
    """
    permission_classes = [IsWorkspaceOperator]

    def get(self, request, conv_id):
        try:
            conv = Conversation.objects.get(id=conv_id, workspace__memberships__user=request.user)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        sla = getattr(conv, 'sla', None)
        if not sla:
            return Response(None)
        return Response(ConversationSLASerializer(sla).data)
