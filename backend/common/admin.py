from django.contrib import admin

from .models import SchedulerHeartbeat


@admin.register(SchedulerHeartbeat)
class SchedulerHeartbeatAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'last_run_at', 'detail')
    readonly_fields = ('name', 'status', 'last_run_at', 'detail')
