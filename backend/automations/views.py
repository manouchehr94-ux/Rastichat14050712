from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardPagination
from common.permissions import IsWorkspaceAdminOfObject, require_workspace_admin
from common.tenancy import resolve_operator_workspace
from conversations.models import Conversation

from .models import AutomationRule, AutomationExecution, ScheduledAction
from .schema import CONDITION_FIELDS, TYPE_OPERATORS, ACTION_PARAM_SPECS, VALUELESS_OPERATORS, validate_rule_payload
from .serializers import AutomationRuleSerializer, AutomationExecutionSerializer, ScheduledActionSerializer
from .simulation import simulate

_CONV_SELECT_RELATED = ('workspace', 'visitor', 'assigned_to', 'team', 'queue', 'sla', 'sla__policy', 'sla__policy__calendar')


def _admin_workspace_ids(user):
    """Workspaces where this user is specifically Owner/Admin — never
    'any workspace they belong to'. Automation rules/history/scheduled
    actions are workspace-admin-only, so the queryset itself must never
    include a workspace where the user is merely an operator, even if
    they happen to be admin somewhere else (the exact cross-workspace
    leak shape the PR #3 audit fixed for the supervisor summary).
    """
    return user.workspace_memberships.filter(
        role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN'],
    ).values_list('workspace_id', flat=True)


def _resolve_conversation_or_none(conv_id, workspace):
    if not conv_id:
        return None, None
    try:
        conv = Conversation.objects.select_related(*_CONV_SELECT_RELATED).get(id=conv_id, workspace=workspace)
    except (Conversation.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        return None, Response({'error': 'Conversation not found in this workspace.'}, status=status.HTTP_404_NOT_FOUND)
    return conv, None


def _readable_param_type(rule):
    if 'choices' in rule:
        return {'kind': 'choice', 'choices': list(rule['choices'])}
    if rule.get('ref'):
        return {'kind': 'reference', 'ref': rule['ref']}
    py_type = rule.get('type')
    if py_type in (None,):
        return {'kind': 'any'}
    if py_type is str:
        return {'kind': 'string'}
    if py_type is dict:
        return {'kind': 'object'}
    if isinstance(py_type, tuple) or py_type in (int, float):
        return {'kind': 'number'}
    return {'kind': 'any'}


class AutomationRuleViewSet(viewsets.ModelViewSet):
    """Workspace-admin-only CRUD for automation rules (spec section 11).
    Reuses the same object-level permission design (IsWorkspaceAdminOfObject)
    added after PR #3's security audit, plus an explicit admin-role-scoped
    queryset so list/retrieve can never leak a rule from a workspace where
    the requester is only an operator.
    """
    serializer_class = AutomationRuleSerializer
    pagination_class = StandardPagination
    permission_classes = [IsWorkspaceAdminOfObject]

    def get_queryset(self):
        qs = AutomationRule.objects.filter(workspace_id__in=_admin_workspace_ids(self.request.user))
        trigger_type = self.request.query_params.get('trigger_type')
        if trigger_type:
            qs = qs.filter(trigger_type=trigger_type)
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ('1', 'true', 'yes'))
        return qs

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        rule = serializer.save(workspace=workspace, created_by=request.user, updated_by=request.user)
        return Response(self.get_serializer(rule).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        rule = self.get_object()
        rule.is_active = True
        rule.save(update_fields=['is_active', 'updated_at'])
        return Response(self.get_serializer(rule).data)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        rule = self.get_object()
        rule.is_active = False
        rule.save(update_fields=['is_active', 'updated_at'])
        return Response(self.get_serializer(rule).data)

    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        rule = self.get_object()
        clone = AutomationRule.objects.create(
            workspace=rule.workspace, name=f'{rule.name} (کپی)', description=rule.description,
            is_active=False, trigger_type=rule.trigger_type, conditions=rule.conditions, actions=rule.actions,
            priority=rule.priority, stop_processing=rule.stop_processing,
            created_by=request.user, updated_by=request.user,
        )
        return Response(self.get_serializer(clone).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def simulate(self, request, pk=None):
        """Dry-run this already-saved rule against an optional target
        conversation. No mutation, no message, no notification, no
        assignment, no scheduled job — see automations/simulation.py.
        """
        rule = self.get_object()
        conv, error_response = _resolve_conversation_or_none(request.data.get('conversation_id'), rule.workspace)
        if error_response:
            return error_response
        result = simulate(rule.conditions, rule.actions, conv, rule.trigger_type, payload=request.data.get('payload') or {})
        return Response(result)


class RuleValidationView(APIView):
    """Validate a draft rule's conditions/actions without saving anything —
    spec section 11's standalone "validation" endpoint, used by the rule
    builder UI before a rule is created or updated.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        conditions = request.data.get('conditions') or {}
        actions = request.data.get('actions') or []
        try:
            validate_rule_payload(conditions, actions, workspace=workspace)
        except DRFValidationError as exc:
            return Response({'valid': False, 'errors': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'valid': True})


class RuleSimulationView(APIView):
    """Dry-run a draft (not-yet-saved) rule — same zero-side-effect
    guarantee as AutomationRuleViewSet.simulate, for the "try before you
    save" flow in the rule builder.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        conditions = request.data.get('conditions') or {}
        actions = request.data.get('actions') or []
        trigger_type = request.data.get('trigger_type')
        if trigger_type not in AutomationRule.Trigger.values:
            return Response({'error': 'Unknown or missing trigger_type.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_rule_payload(conditions, actions, workspace=workspace)
        except DRFValidationError as exc:
            return Response({'error': exc.detail}, status=status.HTTP_400_BAD_REQUEST)

        conv, error_response = _resolve_conversation_or_none(request.data.get('conversation_id'), workspace)
        if error_response:
            return error_response
        result = simulate(conditions, actions, conv, trigger_type, payload=request.data.get('payload') or {})
        return Response(result)


class AutomationRegistryView(APIView):
    """Read-only reference data for the rule builder UI: available
    triggers, condition fields (with their allowed operators), and action
    types (with their parameter shapes). Static/derived from schema.py —
    never exposes anything workspace-specific beyond confirming the caller
    is an admin of the workspace they ask about.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        workspace = resolve_operator_workspace(request.user, request.query_params.get('workspace'))
        require_workspace_admin(request.user, workspace)
        return Response({
            'triggers': [{'value': value, 'label': label} for value, label in AutomationRule.Trigger.choices],
            'condition_fields': {
                field: {
                    'type': field_type,
                    'operators': sorted(TYPE_OPERATORS.get(field_type, set())),
                }
                for field, field_type in CONDITION_FIELDS.items()
            },
            'valueless_operators': sorted(VALUELESS_OPERATORS),
            'actions': {
                action_type: {
                    'params': {name: _readable_param_type(rule) | {'required': bool(rule.get('required'))} for name, rule in spec.items()},
                }
                for action_type, spec in ACTION_PARAM_SPECS.items()
            },
        })


class AutomationExecutionListView(APIView):
    """Workspace-admin-inspectable execution history (spec section 10) —
    no customer-facing route exposes this, and it is filtered strictly by
    admin-role workspace membership, same as AutomationRuleViewSet.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request):
        workspace = resolve_operator_workspace(request.user, request.query_params.get('workspace'))
        require_workspace_admin(request.user, workspace)
        qs = AutomationExecution.objects.filter(workspace=workspace).select_related('rule').prefetch_related('action_executions')

        rule_id = request.query_params.get('rule')
        if rule_id:
            qs = qs.filter(rule_id=rule_id)
        conv_id = request.query_params.get('conversation')
        if conv_id:
            qs = qs.filter(conversation_id=conv_id)
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        correlation_id = request.query_params.get('correlation_id')
        if correlation_id:
            qs = qs.filter(correlation_id=correlation_id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = AutomationExecutionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ScheduledActionListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request):
        workspace = resolve_operator_workspace(request.user, request.query_params.get('workspace'))
        require_workspace_admin(request.user, workspace)
        qs = ScheduledAction.objects.filter(workspace=workspace).select_related('rule')

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        conv_id = request.query_params.get('conversation')
        if conv_id:
            qs = qs.filter(conversation_id=conv_id)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = ScheduledActionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ScheduledActionCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from django.utils import timezone
        try:
            scheduled = ScheduledAction.objects.select_related('workspace').get(pk=pk)
        except (ScheduledAction.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        require_workspace_admin(request.user, scheduled.workspace)
        if scheduled.status != ScheduledAction.Status.PENDING:
            return Response({'error': 'Only a pending scheduled action can be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        scheduled.status = ScheduledAction.Status.CANCELLED
        scheduled.cancelled_at = timezone.now()
        scheduled.save(update_fields=['status', 'cancelled_at'])
        return Response(ScheduledActionSerializer(scheduled).data)
