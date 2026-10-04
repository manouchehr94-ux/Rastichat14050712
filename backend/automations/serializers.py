from rest_framework import serializers

from .models import AutomationRule, AutomationExecution, AutomationActionExecution, ScheduledAction
from .schema import validate_rule_payload


class AutomationRuleSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField(read_only=True)
    updated_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AutomationRule
        fields = [
            'id', 'workspace', 'name', 'description', 'is_active', 'trigger_type',
            'conditions', 'actions', 'schema_version', 'priority', 'stop_processing',
            'created_by', 'created_by_name', 'updated_by', 'updated_by_name',
            'created_at', 'updated_at', 'last_executed_at', 'execution_count',
            'valid_from', 'valid_until',
        ]
        read_only_fields = [
            'id', 'workspace', 'schema_version', 'created_by', 'updated_by',
            'created_at', 'updated_at', 'last_executed_at', 'execution_count',
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.display_name if obj.created_by_id else None

    def get_updated_by_name(self, obj):
        return obj.updated_by.display_name if obj.updated_by_id else None

    def validate(self, attrs):
        workspace = self.instance.workspace if self.instance else self.context.get('workspace')
        conditions = attrs.get('conditions', self.instance.conditions if self.instance else {})
        actions = attrs.get('actions', self.instance.actions if self.instance else [])
        validate_rule_payload(conditions, actions, workspace=workspace)

        valid_from = attrs.get('valid_from', getattr(self.instance, 'valid_from', None))
        valid_until = attrs.get('valid_until', getattr(self.instance, 'valid_until', None))
        if valid_from and valid_until and valid_from >= valid_until:
            raise serializers.ValidationError({'valid_until': '"valid_until" must be after "valid_from".'})
        return attrs


class AutomationActionExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationActionExecution
        fields = [
            'id', 'action_index', 'action_type', 'status', 'result_summary',
            'affected_object_type', 'affected_object_id', 'error_summary', 'created_at',
        ]
        read_only_fields = fields


class AutomationExecutionSerializer(serializers.ModelSerializer):
    rule_name = serializers.CharField(source='rule_name_snapshot', read_only=True)
    action_executions = AutomationActionExecutionSerializer(many=True, read_only=True)

    class Meta:
        model = AutomationExecution
        fields = [
            'id', 'workspace', 'rule', 'rule_name', 'trigger_type', 'event_id',
            'correlation_id', 'depth', 'conversation', 'status', 'condition_result',
            'actor_type', 'actor_id', 'duration_ms', 'error_summary', 'is_simulation',
            'created_at', 'action_executions',
        ]
        read_only_fields = fields


class ScheduledActionSerializer(serializers.ModelSerializer):
    rule_name = serializers.CharField(source='rule.name', read_only=True, default=None)

    class Meta:
        model = ScheduledAction
        fields = [
            'id', 'workspace', 'rule', 'rule_name', 'conversation', 'action_definition',
            'correlation_id', 'depth', 'execute_at', 'status', 'attempts', 'max_attempts',
            'last_error', 'locked_at', 'locked_by', 'executed_at', 'cancelled_at', 'created_at',
        ]
        read_only_fields = fields
