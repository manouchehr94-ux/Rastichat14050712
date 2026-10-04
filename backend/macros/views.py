from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from common.pagination import StandardPagination
from common.tenancy import resolve_operator_workspace
from conversations.models import Conversation

from . import services
from .models import Macro, MacroExecution
from .permissions import (
    can_create_macro, require_can_execute_macro, require_can_manage_macro, require_can_view_macro,
)
from .schema import ACTION_PARAM_SPECS
from .serializers import MacroExecutionSerializer, MacroSerializer


def _visible_macro_ids(user, workspace_id):
    """Every macro id `user` is authorized to VIEW in this exact workspace —
    computed once via the shared permission predicate rather than a
    hand-rolled duplicate queryset filter, so list/retrieve can never drift
    out of sync with can_view_macro's actual rule.
    """
    from .permissions import can_view_macro
    return [m.id for m in Macro.objects.filter(workspace_id=workspace_id) if can_view_macro(user, m)]


class MacroViewSet(viewsets.ModelViewSet):
    serializer_class = MacroSerializer
    pagination_class = StandardPagination
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        workspace_id = self.request.query_params.get('workspace')
        member_workspace_ids = list(self.request.user.workspace_memberships.values_list('workspace_id', flat=True))
        qs = Macro.objects.filter(workspace_id__in=member_workspace_ids)
        if workspace_id:
            qs = qs.filter(workspace_id=workspace_id)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ('1', 'true', 'yes'))
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        ids = set()
        for wsid in {m['workspace_id'] for m in qs.values('workspace_id')}:
            ids.update(_visible_macro_ids(self.request.user, wsid))
        return qs.filter(id__in=ids)

    def get_object(self):
        obj = super().get_object()
        require_can_view_macro(self.request.user, obj)
        return obj

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        visibility = request.data.get('visibility', Macro.Visibility.WORKSPACE)
        team = None
        if request.data.get('team'):
            from teams.models import Team
            team = Team.objects.filter(id=request.data['team'], workspace=workspace).first()
            if team is None:
                return Response({'error': 'Team not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        if not can_create_macro(request.user, workspace, visibility, team=team):
            raise PermissionDenied('Not authorized to create a macro with this visibility')
        owner = request.user if visibility == Macro.Visibility.PRIVATE else None
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        macro = serializer.save(workspace=workspace, owner=owner, team=team, created_by=request.user, updated_by=request.user)
        return Response(self.get_serializer(macro).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        macro = self.get_object()
        require_can_manage_macro(request.user, macro)
        from common.permissions import user_has_workspace_role
        data = request.data
        if not user_has_workspace_role(request.user, macro.workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']):
            # A non-admin owner may edit content/state on their own PRIVATE
            # macro, but must never be able to change visibility/owner/team
            # via this endpoint — that would let an operator self-promote a
            # PRIVATE macro to WORKSPACE (or TEAM) visibility without the
            # authorization create() itself would have required.
            data = {k: v for k, v in request.data.items() if k not in ('visibility', 'owner', 'team')}
        serializer = self.get_serializer(macro, data=data, partial=kwargs.get('partial', False), context={**self.get_serializer_context(), 'workspace': macro.workspace})
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        macro = self.get_object()
        require_can_manage_macro(request.user, macro)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        macro = self.get_object()
        require_can_manage_macro(request.user, macro)
        macro.is_active = True
        macro.save(update_fields=['is_active', 'updated_at'])
        return Response(self.get_serializer(macro).data)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        macro = self.get_object()
        require_can_manage_macro(request.user, macro)
        macro.is_active = False
        macro.save(update_fields=['is_active', 'updated_at'])
        return Response(self.get_serializer(macro).data)

    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        source = self.get_object()
        require_can_manage_macro(request.user, source)
        clone = Macro.objects.create(
            workspace=source.workspace, name=f'{source.name} (کپی)', description=source.description,
            is_active=False, visibility=source.visibility, owner=source.owner, team=source.team,
            category=source.category, actions=source.actions,
            created_by=request.user, updated_by=request.user,
        )
        return Response(self.get_serializer(clone).data, status=status.HTTP_201_CREATED)

    def _resolve_conversation(self, request, macro):
        conv_id = request.data.get('conversation_id')
        try:
            return Conversation.objects.get(id=conv_id, workspace=macro.workspace)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            return None

    @action(detail=True, methods=['post'])
    def preview(self, request, pk=None):
        macro = self.get_object()
        conversation = self._resolve_conversation(request, macro)
        if conversation is None:
            return Response({'error': 'Conversation not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        try:
            result = services.preview_macro(macro, conversation)
        except services.MacroError as exc:
            return Response({'error': exc.message}, status=exc.status_code)
        return Response(result)

    def get_throttles(self):
        # `throttle_scope` isn't a pre-declared attribute on APIView, so it
        # can't be passed as an `@action(...)` kwarg the way `detail`/
        # `methods` can (DRF's ViewSet.as_view() rejects initkwargs that
        # aren't already class attributes) — set it dynamically here,
        # scoped to just the `execute` action, instead.
        if self.action == 'execute':
            self.throttle_scope = 'macro_execution'
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @action(detail=True, methods=['post'])
    def execute(self, request, pk=None):
        macro = self.get_object()
        require_can_execute_macro(request.user, macro)
        conversation = self._resolve_conversation(request, macro)
        if conversation is None:
            return Response({'error': 'Conversation not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        idempotency_key = request.data.get('idempotency_key')
        try:
            execution = services.execute_macro(macro, conversation, request.user, idempotency_key)
        except services.MacroError as exc:
            return Response({'error': exc.message}, status=exc.status_code)
        return Response(MacroExecutionSerializer(execution).data, status=status.HTTP_200_OK)


def _readable_param_type(rule):
    if 'choices' in rule:
        return {'kind': 'choice', 'choices': list(rule['choices'])}
    if rule.get('ref'):
        return {'kind': 'reference', 'ref': rule['ref']}
    py_type = rule.get('type')
    if py_type is None:
        return {'kind': 'any'}
    if py_type is str:
        return {'kind': 'string', 'max_len': rule.get('max_len')}
    return {'kind': 'any'}


class MacroRegistryView(APIView):
    """Read-only reference data for the macro action-builder UI — the
    supported action types and their parameter shapes, mirroring
    automations.views.AutomationRegistryView.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({
            'actions': {
                action_type: {
                    'params': {
                        name: _readable_param_type(rule) | {'required': bool(rule.get('required'))}
                        for name, rule in spec.items()
                    },
                }
                for action_type, spec in ACTION_PARAM_SPECS.items()
            },
            'variables': sorted([
                'customer_name', 'store_name', 'conversation_id', 'agent_name', 'queue_name', 'team_name',
                'article_title', 'order_number', 'product_name',
            ]),
        })


class MacroExecutionListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request):
        workspace = resolve_operator_workspace(request.user, request.query_params.get('workspace'))
        from common.permissions import user_has_workspace_role
        if not user_has_workspace_role(request.user, workspace.id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN', 'WORKSPACE_OPERATOR']):
            raise PermissionDenied('Not a member of this workspace')
        qs = MacroExecution.objects.filter(workspace=workspace).select_related('macro').prefetch_related('action_executions')
        macro_id = request.query_params.get('macro')
        if macro_id:
            qs = qs.filter(macro_id=macro_id)
        conv_id = request.query_params.get('conversation')
        if conv_id:
            qs = qs.filter(conversation_id=conv_id)
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(MacroExecutionSerializer(page, many=True).data)


class MacroExecutionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            execution = MacroExecution.objects.select_related('macro').prefetch_related('action_executions').get(pk=pk)
        except (MacroExecution.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        from common.permissions import user_has_workspace_role
        if not user_has_workspace_role(request.user, execution.workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN', 'WORKSPACE_OPERATOR']):
            raise PermissionDenied('Not a member of this workspace')
        return Response(MacroExecutionSerializer(execution).data)

    def post(self, request, pk):
        """Retry — re-runs only the actions that never succeeded."""
        try:
            execution = MacroExecution.objects.select_related('macro').get(pk=pk)
        except (MacroExecution.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        require_can_execute_macro(request.user, execution.macro)
        try:
            execution = services.retry_macro_execution(execution, request.user)
        except services.MacroError as exc:
            return Response({'error': exc.message}, status=exc.status_code)
        return Response(MacroExecutionSerializer(execution).data)
