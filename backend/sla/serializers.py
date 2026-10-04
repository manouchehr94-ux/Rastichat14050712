from rest_framework import serializers
from .models import BusinessCalendar, WorkingInterval, Holiday, SLAPolicy, ConversationSLA


class WorkingIntervalSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkingInterval
        fields = ['id', 'calendar', 'weekday', 'start_time', 'end_time']
        read_only_fields = ['id', 'calendar']


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ['id', 'calendar', 'date', 'name', 'is_working', 'start_time', 'end_time']
        read_only_fields = ['id', 'calendar']


class BusinessCalendarSerializer(serializers.ModelSerializer):
    working_intervals = WorkingIntervalSerializer(many=True, read_only=True)
    holidays = HolidaySerializer(many=True, read_only=True)

    class Meta:
        model = BusinessCalendar
        fields = ['id', 'workspace', 'name', 'timezone', 'working_intervals', 'holidays', 'created_at', 'updated_at']
        read_only_fields = ['id', 'workspace', 'created_at', 'updated_at']


class SLAPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SLAPolicy
        fields = [
            'id', 'workspace', 'name', 'queue', 'team', 'priority', 'calendar',
            'first_response_target_minutes', 'next_response_target_minutes', 'resolution_target_minutes',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'workspace', 'created_at', 'updated_at']

    def validate(self, attrs):
        workspace = self.instance.workspace if self.instance else self.context.get('workspace')
        for field in ('queue', 'team', 'calendar'):
            value = attrs.get(field, getattr(self.instance, field, None) if self.instance else None)
            if value and workspace and value.workspace_id != workspace.id:
                raise serializers.ValidationError({field: f'{field.capitalize()} must belong to the same workspace as the policy.'})
        return attrs


class ConversationSLASerializer(serializers.ModelSerializer):
    policy_name = serializers.CharField(source='policy_name_snapshot', read_only=True)

    class Meta:
        model = ConversationSLA
        fields = [
            'conversation', 'policy', 'policy_name', 'first_response_target_minutes',
            'next_response_target_minutes', 'resolution_target_minutes',
            'first_response_due_at', 'next_response_due_at', 'resolution_due_at',
            'first_responded_at', 'resolved_at',
            'first_response_breached_at', 'next_response_breached_at', 'resolution_breached_at',
        ]
        read_only_fields = fields
