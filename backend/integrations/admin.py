from django.contrib import admin
from django.utils import timezone

from . import audit
from .models import Integration, IntegrationKey, IntegrationTenantMapping


class IntegrationKeyInline(admin.TabularInline):
    model = IntegrationKey
    extra = 0
    fields = ('kid', 'status', 'scopes', 'not_before', 'expires_at', 'last_used_at', 'revoked_at')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False  # keys are added with `manage.py integration_key_add` (so the audit trail is complete)


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'platform', 'is_active', 'created_at')
    list_filter = ('is_active',)
    inlines = [IntegrationKeyInline]

    def save_model(self, request, obj, form, change):
        was_active = None
        if change:
            was_active = Integration.objects.filter(pk=obj.pk).values_list('is_active', flat=True).first()
        if not obj.is_active and obj.disabled_at is None:
            obj.disabled_at = timezone.now()
        if obj.is_active:
            obj.disabled_at = None
        super().save_model(request, obj, form, change)
        if not change:
            audit.record('created', integration=obj, target_id=obj.pk, actor=request.user)
        elif was_active is not None and was_active != obj.is_active:
            audit.record('enabled' if obj.is_active else 'disabled', integration=obj, target_id=obj.pk, actor=request.user)


@admin.register(IntegrationTenantMapping)
class IntegrationTenantMappingAdmin(admin.ModelAdmin):
    list_display = ('integration', 'external_tenant_id', 'status', 'workspace', 'last_synced_at')
    list_filter = ('integration', 'status')
    search_fields = ('external_tenant_id', 'display_name')
    readonly_fields = [f.name for f in IntegrationTenantMapping._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
