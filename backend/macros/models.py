import uuid

from django.conf import settings
from django.db import models

from workspaces.models import Workspace


class Macro(models.Model):
    """A persistent, declarative sequence of Help Desk actions an
    authorized user can run with one deliberate command. `actions` is a
    strictly schema-validated JSON list (see macros/schema.py) — never
    executable code, mirroring AutomationRule.actions exactly. There is no
    separate "MacroAction" table: deterministic order is simply list order,
    the same convention automations.AutomationRule already uses and this
    phase was told to reuse.
    """

    class Visibility(models.TextChoices):
        PRIVATE = 'PRIVATE', 'Private (owner only)'
        TEAM = 'TEAM', 'Team'
        WORKSPACE = 'WORKSPACE', 'Workspace'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='macros')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=False)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.WORKSPACE)
    # Required when visibility=PRIVATE (only the owner may see/run it),
    # optional context otherwise.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='owned_macros',
    )
    # Required when visibility=TEAM.
    team = models.ForeignKey('teams.Team', on_delete=models.CASCADE, null=True, blank=True, related_name='macros')
    category = models.CharField(max_length=100, blank=True, default='')
    shortcut = models.CharField(max_length=50, blank=True, default='')
    actions = models.JSONField(default=list, blank=True)
    schema_version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='macros_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='macros_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    execution_count = models.PositiveIntegerField(default=0)
    last_executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['category', 'name']
        indexes = [
            models.Index(fields=['workspace', 'is_active', 'visibility'], name='macro_ws_active_vis_idx'),
        ]
        constraints = [
            # A plain unique_together on ('workspace', 'shortcut') would
            # reject a second BLANK shortcut in the same workspace — '' is a
            # real, non-NULL value to Postgres, not an "unset" wildcard like
            # NULL is. Only actually-set shortcuts need to be unique.
            models.UniqueConstraint(
                fields=['workspace', 'shortcut'], condition=~models.Q(shortcut=''),
                name='macro_shortcut_unique_when_set',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'


class MacroExecution(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUCCEEDED = 'SUCCEEDED', 'Succeeded'
        PARTIALLY_SUCCEEDED = 'PARTIALLY_SUCCEEDED', 'Partially succeeded'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    macro = models.ForeignKey(Macro, on_delete=models.CASCADE, related_name='executions')
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='macro_executions')
    conversation = models.ForeignKey(
        'conversations.Conversation', on_delete=models.SET_NULL, null=True, blank=True, related_name='macro_executions',
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='macro_executions',
    )
    # A double-click or a retried HTTP request carries the SAME
    # idempotency_key — the unique constraint below is what makes a second
    # execute() call with that key a no-op re-read of the first result
    # instead of a second run. Client-generated (e.g. a UUID minted once per
    # button click), never server-derived from mutable state.
    idempotency_key = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    actions_snapshot = models.JSONField(default=list, blank=True)
    error_summary = models.CharField(max_length=500, blank=True, default='')
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('workspace', 'idempotency_key')
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['workspace', 'macro'], name='macroexec_ws_macro_idx'),
            models.Index(fields=['workspace', 'conversation'], name='macroexec_ws_conv_idx'),
        ]


class MacroActionExecution(models.Model):
    class Status(models.TextChoices):
        SUCCEEDED = 'SUCCEEDED', 'Succeeded'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution = models.ForeignKey(MacroExecution, on_delete=models.CASCADE, related_name='action_executions')
    action_index = models.PositiveIntegerField()
    action_type = models.CharField(max_length=50)
    params_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    result_summary = models.JSONField(default=dict, blank=True)
    error_summary = models.CharField(max_length=500, blank=True, default='')
    affected_object_type = models.CharField(max_length=50, blank=True, default='')
    affected_object_id = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('execution', 'action_index')
        ordering = ['action_index']
