from rest_framework import serializers

from .models import Macro, MacroActionExecution, MacroExecution
from .schema import validate_actions


class MacroSerializer(serializers.ModelSerializer):
    class Meta:
        model = Macro
        fields = [
            'id', 'workspace', 'name', 'description', 'is_active', 'visibility', 'owner', 'team',
            'category', 'shortcut', 'actions', 'schema_version', 'created_by', 'updated_by',
            'created_at', 'updated_at', 'execution_count', 'last_executed_at',
        ]
        read_only_fields = [
            'id', 'workspace', 'created_by', 'updated_by', 'created_at', 'updated_at',
            'execution_count', 'last_executed_at',
        ]

    def validate_actions(self, value):
        workspace = self.context.get('workspace') or (self.instance.workspace if self.instance else None)
        validate_actions(value, workspace=workspace)
        return value

    def validate(self, attrs):
        visibility = attrs.get('visibility', self.instance.visibility if self.instance else Macro.Visibility.WORKSPACE)
        team = attrs.get('team', self.instance.team if self.instance else None)
        if visibility == Macro.Visibility.TEAM and team is None:
            raise serializers.ValidationError({'team': 'A team-visibility macro requires a team.'})
        return attrs


class MacroActionExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MacroActionExecution
        fields = [
            'id', 'action_index', 'action_type', 'params_snapshot', 'status', 'result_summary',
            'error_summary', 'affected_object_type', 'affected_object_id', 'created_at',
        ]
        read_only_fields = fields


class MacroExecutionSerializer(serializers.ModelSerializer):
    action_executions = MacroActionExecutionSerializer(many=True, read_only=True)

    class Meta:
        model = MacroExecution
        fields = [
            'id', 'macro', 'workspace', 'conversation', 'executed_by', 'idempotency_key', 'status',
            'actions_snapshot', 'error_summary', 'action_executions', 'started_at', 'completed_at',
        ]
        read_only_fields = fields
