from django.contrib import admin

from .models import AutomationRule, AutomationEvent, AutomationExecution, AutomationActionExecution, ScheduledAction


@admin.register(AutomationRule)
class AutomationRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'trigger_type', 'is_active', 'priority', 'execution_count', 'last_executed_at')
    list_filter = ('trigger_type', 'is_active', 'workspace')
    search_fields = ('name', 'description')


@admin.register(AutomationExecution)
class AutomationExecutionAdmin(admin.ModelAdmin):
    list_display = ('rule_name_snapshot', 'workspace', 'trigger_type', 'status', 'conversation', 'created_at')
    list_filter = ('status', 'trigger_type', 'workspace')
    readonly_fields = [f.name for f in AutomationExecution._meta.fields]


@admin.register(AutomationActionExecution)
class AutomationActionExecutionAdmin(admin.ModelAdmin):
    list_display = ('execution', 'action_index', 'action_type', 'status', 'created_at')
    list_filter = ('status', 'action_type')


@admin.register(ScheduledAction)
class ScheduledActionAdmin(admin.ModelAdmin):
    list_display = ('id', 'workspace', 'status', 'execute_at', 'attempts', 'max_attempts', 'conversation')
    list_filter = ('status', 'workspace')


@admin.register(AutomationEvent)
class AutomationEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'event_type', 'workspace', 'correlation_id', 'processed_at')
    list_filter = ('event_type', 'workspace')
