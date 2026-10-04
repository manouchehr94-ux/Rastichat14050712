import uuid
from django.conf import settings
from django.db import models
from workspaces.models import Workspace


class AutomationRule(models.Model):
    """A persistent, workspace-scoped automation rule: WHEN <trigger_type>
    fires, IF <conditions> match, THEN run <actions> in order.

    `conditions`/`actions` are strictly schema-validated JSON trees (see
    automations/schema.py) — never executable code. All validation happens
    at the serializer layer before a rule is ever saved; nothing here
    trusts unvalidated JSON at evaluation time either (the engine
    re-validates defensively).
    """
    class Trigger(models.TextChoices):
        CONVERSATION_CREATED = 'CONVERSATION_CREATED', 'Conversation created'
        CONVERSATION_QUEUED = 'CONVERSATION_QUEUED', 'Conversation queued'
        CONVERSATION_ASSIGNED = 'CONVERSATION_ASSIGNED', 'Conversation assigned'
        CONVERSATION_UNASSIGNED = 'CONVERSATION_UNASSIGNED', 'Conversation unassigned'
        CONVERSATION_TRANSFERRED = 'CONVERSATION_TRANSFERRED', 'Conversation transferred'
        CONVERSATION_ESCALATED = 'CONVERSATION_ESCALATED', 'Conversation escalated'
        CONVERSATION_PRIORITY_CHANGED = 'CONVERSATION_PRIORITY_CHANGED', 'Priority changed'
        CONVERSATION_STATUS_CHANGED = 'CONVERSATION_STATUS_CHANGED', 'Status changed'
        CUSTOMER_MESSAGE_CREATED = 'CUSTOMER_MESSAGE_CREATED', 'Customer message created'
        OPERATOR_MESSAGE_CREATED = 'OPERATOR_MESSAGE_CREATED', 'Operator message created'
        INTERNAL_NOTE_CREATED = 'INTERNAL_NOTE_CREATED', 'Internal note created'
        SLA_APPROACHING = 'SLA_APPROACHING', 'SLA approaching'
        SLA_BREACHED = 'SLA_BREACHED', 'SLA breached'
        RATING_SUBMITTED = 'RATING_SUBMITTED', 'Rating submitted'
        CONVERSATION_RESOLVED = 'CONVERSATION_RESOLVED', 'Conversation resolved'
        CONVERSATION_CLOSED = 'CONVERSATION_CLOSED', 'Conversation closed'
        CONVERSATION_REOPENED = 'CONVERSATION_REOPENED', 'Conversation reopened'
        SCHEDULED_TIME_REACHED = 'SCHEDULED_TIME_REACHED', 'Scheduled time reached'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='automation_rules')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=False)
    trigger_type = models.CharField(max_length=40, choices=Trigger.choices)
    conditions = models.JSONField(default=dict, blank=True)
    actions = models.JSONField(default=list, blank=True)
    schema_version = models.PositiveIntegerField(default=1)
    # Lower number = evaluated first, same convention as queues.Queue.routing_priority.
    priority = models.IntegerField(default=100)
    stop_processing = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='automation_rules_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='automation_rules_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_executed_at = models.DateTimeField(null=True, blank=True)
    execution_count = models.PositiveIntegerField(default=0)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        # `id` is a final, stable tie-breaker for rules sharing the same
        # priority and created_at (e.g. bulk-seeded fixtures with identical
        # timestamps) — without it, ordering among ties is undefined
        # (physical row order), not deterministic.
        ordering = ['priority', 'created_at', 'id']
        indexes = [
            models.Index(fields=['workspace', 'trigger_type', 'is_active', 'priority'], name='autorule_lookup_idx'),
        ]

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'


class AutomationEvent(models.Model):
    """Idempotency guard for the canonical trigger-event envelope: an
    event_id is recorded (and thus fully processed) at most once, no matter
    how many times publish_event() is called with it.
    """
    event_id = models.UUIDField(primary_key=True)
    event_type = models.CharField(max_length=40)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='automation_events')
    correlation_id = models.UUIDField()
    depth = models.PositiveIntegerField(default=0)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['correlation_id'], name='autoevent_correlation_idx'),
        ]


class AutomationExecution(models.Model):
    """One row per (event, rule) evaluation attempt — the audit trail
    required for every rule evaluation, matched or not.
    """
    class Status(models.TextChoices):
        MATCHED = 'MATCHED', 'Matched'
        NOT_MATCHED = 'NOT_MATCHED', 'Not matched'
        SUCCEEDED = 'SUCCEEDED', 'Succeeded'
        PARTIALLY_SUCCEEDED = 'PARTIALLY_SUCCEEDED', 'Partially succeeded'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'
        SKIPPED_LOOP = 'SKIPPED_LOOP', 'Skipped (loop protection)'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.BigAutoField(primary_key=True)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='automation_executions')
    rule = models.ForeignKey(AutomationRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='executions')
    # Preserved even if the rule is later renamed/deleted — audit history
    # must remain readable independent of the rule's current state.
    rule_name_snapshot = models.CharField(max_length=255, blank=True, default='')
    trigger_type = models.CharField(max_length=40)
    event_id = models.UUIDField()
    correlation_id = models.UUIDField()
    depth = models.PositiveIntegerField(default=0)
    conversation = models.ForeignKey(
        'conversations.Conversation', on_delete=models.SET_NULL, null=True, blank=True, related_name='automation_executions',
    )
    status = models.CharField(max_length=25, choices=Status.choices)
    condition_result = models.BooleanField(null=True, blank=True)
    actor_type = models.CharField(max_length=20, blank=True, default='SYSTEM')
    actor_id = models.CharField(max_length=64, blank=True, default='')
    duration_ms = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True, default='')
    is_simulation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workspace', 'created_at'], name='autoexec_workspace_idx'),
            models.Index(fields=['correlation_id'], name='autoexec_correlation_idx'),
            models.Index(fields=['rule', 'correlation_id'], name='autoexec_rule_corr_idx'),
        ]


class AutomationActionExecution(models.Model):
    """One row per individual action attempted within an AutomationExecution."""
    class Status(models.TextChoices):
        SUCCEEDED = 'SUCCEEDED', 'Succeeded'
        FAILED = 'FAILED', 'Failed'
        SKIPPED = 'SKIPPED', 'Skipped'

    id = models.BigAutoField(primary_key=True)
    execution = models.ForeignKey(AutomationExecution, on_delete=models.CASCADE, related_name='action_executions')
    action_index = models.PositiveIntegerField()
    action_type = models.CharField(max_length=40)
    status = models.CharField(max_length=15, choices=Status.choices)
    # Safe, non-secret summary only (e.g. {"assigned_to": "<user id>"}) —
    # never raw stack traces or credentials.
    result_summary = models.JSONField(default=dict, blank=True)
    affected_object_type = models.CharField(max_length=40, blank=True, default='')
    affected_object_id = models.CharField(max_length=64, blank=True, default='')
    error_summary = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['action_index']


class ScheduledAction(models.Model):
    """A persisted, delayed automation action, processed by
    `python manage.py process_automation_jobs`.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        RUNNING = 'RUNNING', 'Running'
        SUCCEEDED = 'SUCCEEDED', 'Succeeded'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        SKIPPED = 'SKIPPED', 'Skipped'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='scheduled_actions')
    rule = models.ForeignKey(AutomationRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='scheduled_actions')
    conversation = models.ForeignKey(
        'conversations.Conversation', on_delete=models.CASCADE, null=True, blank=True, related_name='scheduled_actions',
    )
    # A validated single-action definition snapshot — see schema.py.
    action_definition = models.JSONField()
    correlation_id = models.UUIDField()
    depth = models.PositiveIntegerField(default=0)
    execute_at = models.DateTimeField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    last_error = models.TextField(blank=True, default='')
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=64, blank=True, default='')
    executed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Deterministic key derived from (rule, conversation, action, correlation)
    # so re-scheduling the same logical delayed action is a no-op rather
    # than a duplicate row.
    idempotency_key = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['execute_at']
        indexes = [
            models.Index(fields=['status', 'execute_at'], name='autosched_status_time_idx'),
            models.Index(fields=['workspace', 'status'], name='autosched_workspace_idx'),
        ]

    def __str__(self):
        return f'{self.id} @ {self.execute_at} ({self.status})'


class AutomationActionSideEffect(models.Model):
    """Reserve-then-complete ledger enforcing action-level idempotency for
    every side-effecting automation action, independent of (and in addition
    to) the message-specific client_message_id dedup already used by
    SEND_CUSTOMER_MESSAGE/CREATE_INTERNAL_NOTE/REQUEST_RATING.

    idempotency_key is derived from (retry_seed, rule_id, conversation_id,
    action_type, action_index[, recipient_id for multi-recipient actions]) —
    stable across retries of the SAME logical unit of work (a ScheduledAction
    row's id, or an instant execution's id — see ActionRunContext.retry_seed
    — never a per-attempt AutomationExecution id), so a crash-point-B retry
    reuses the identical key and is recognized as already-done.

    Reserve-then-complete: a row is created (reserving the key) BEFORE the
    domain call runs; completed_at is set only after the domain call
    returns successfully. A reservation whose domain call raised is deleted
    immediately (see automations.idempotency.run_idempotent) so a genuine
    failure can still be retried — only a *completed* row blocks a future
    replay. A reservation abandoned mid-flight (the same crash-point-B
    scenario, one level deeper) is reclaimed once reserved_at is older than
    AUTOMATION_JOB_STALE_AFTER_SECONDS, mirroring ScheduledAction's own
    locked_at staleness window.
    """
    idempotency_key = models.CharField(max_length=255, unique=True)
    action_type = models.CharField(max_length=40)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='automation_action_side_effects')
    conversation = models.ForeignKey(
        'conversations.Conversation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='automation_action_side_effects',
    )
    result_summary = models.JSONField(default=dict, blank=True)
    affected_object_type = models.CharField(max_length=40, blank=True, default='')
    affected_object_id = models.CharField(max_length=64, blank=True, default='')
    reserved_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['workspace', 'reserved_at'], name='autoactsfx_workspace_idx'),
        ]


class AutomationCorrelationCounter(models.Model):
    """A single, row-locked counter per correlation_id, making
    MAX_ACTIONS_PER_CORRELATION a hard, concurrency-safe bound instead of
    the previous unlocked `AutomationActionExecution.objects.filter(...).count()`
    read-then-act check (two workers could both read a count below the
    limit and both proceed, overshooting it). A worker must successfully
    reserve a slot here (select_for_update + increment, see
    automations.idempotency.reserve_action_slot) BEFORE it is allowed to run
    an action in this correlation; a refused reservation leaves the counter
    untouched, so the loser never consumes a slot it didn't use.
    """
    correlation_id = models.UUIDField(primary_key=True)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='automation_correlation_counters')
    actions_reserved = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AutomationActionSlotReservation(models.Model):
    """One row per successfully-reserved action slot — the concrete unit
    AutomationCorrelationCounter.actions_reserved counts. reservation_key is
    a stable identifier for one logical action attempt (a ScheduledAction's
    id for the scheduled path; an instant execution's id + action_index for
    the engine path), unique across the whole table: retrying that SAME
    attempt (crash-point-B recovery, or any other retry) finds its own row
    already present and reuses it instead of consuming a second slot — see
    automations.idempotency.reserve_action_slot's "retries of the same
    action must not double-consume the counter" guarantee.
    """
    id = models.BigAutoField(primary_key=True)
    correlation_id = models.UUIDField()
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='automation_action_slot_reservations')
    reservation_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['correlation_id'], name='autoslotrsv_correlation_idx'),
        ]
