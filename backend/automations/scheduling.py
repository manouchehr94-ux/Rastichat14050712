"""Creation side of delayed/scheduled automation actions.

The processing side lives in
`automations/management/commands/process_automation_jobs.py`. This module
only ever creates/cancels `ScheduledAction` rows — it never executes an
action itself, keeping SCHEDULE_ACTION a pure "persist a future intent"
operation (spec section 6).
"""
import hashlib
import uuid

from .models import ScheduledAction


def _derive_idempotency_key(seed):
    # Deterministic, fixed-length, collision-resistant key from an
    # arbitrary-length seed string (rule/conversation/correlation/action
    # index) — re-scheduling the same logical delayed action is therefore a
    # no-op (get_or_create below) rather than a duplicate row.
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()


def schedule_action(*, workspace, rule_id, conversation, action_definition, execute_at, correlation_id, depth,
                     idempotency_seed):
    key = _derive_idempotency_key(idempotency_seed)
    scheduled, _created = ScheduledAction.objects.get_or_create(
        idempotency_key=key,
        defaults=dict(
            id=uuid.uuid4(), workspace=workspace, rule_id=rule_id, conversation=conversation,
            action_definition=action_definition, correlation_id=correlation_id, depth=depth,
            execute_at=execute_at, status=ScheduledAction.Status.PENDING,
        ),
    )
    return scheduled
