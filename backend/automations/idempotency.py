"""Concurrency-safe, database-backed idempotency primitives shared by the
scheduled-action executor (process_automation_jobs) and the instant engine
(engine.py) — see AutomationActionSideEffect / AutomationCorrelationCounter
in models.py for the schema these operate on.
"""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AutomationActionSideEffect, AutomationActionSlotReservation, AutomationCorrelationCounter

STALE_RESERVATION_SECONDS = getattr(settings, 'AUTOMATION_JOB_STALE_AFTER_SECONDS', 300)


class ReservationInProgress(Exception):
    """Another (still-live, non-stale) worker holds this exact idempotency
    key's reservation right now. The caller should treat this the same as
    any other transient action failure — the job/event retries later and,
    by then, either finds a completed result to reuse or the reservation
    has gone stale and can be reclaimed.
    """


def run_idempotent(idempotency_key, action_type, workspace_id, conversation, fn):
    """Run fn() (a zero-arg callable returning (result, obj_type, obj_id))
    at most once for a given idempotency_key, no matter how many times this
    is called with the same key — including across process crashes.

    - No idempotency_key (human-triggered actions never pass one): fn() runs
      unguarded, exactly like before this mechanism existed.
    - First call for a key: reserves the key (INSERT under the unique
      constraint), runs fn(), records the result, returns it.
    - A completed reservation already exists: returns the recorded result
      WITHOUT calling fn() again — this is what makes a crash-point-B retry
      safe for assignment/transfer/escalation/notification actions, not
      just the message-creating ones already protected by client_message_id.
    - A reservation exists but never completed (the worker holding it
      crashed between reserving and finishing) and is older than the stale
      window: reclaimed (deleted) and retried once.
    - fn() raises: the reservation is deleted so a genuine failure (not a
      crash) can still be retried on its own terms next time, instead of
      being permanently blocked by a reservation for work that never
      actually happened.
    """
    if not idempotency_key:
        result, obj_type, obj_id = fn()
        return result, obj_type, obj_id

    now = timezone.now()
    effect = None
    for _attempt in range(2):
        try:
            with transaction.atomic():
                effect = AutomationActionSideEffect.objects.create(
                    idempotency_key=idempotency_key, action_type=action_type,
                    workspace_id=workspace_id, conversation=conversation,
                )
            break
        except IntegrityError:
            existing = AutomationActionSideEffect.objects.filter(idempotency_key=idempotency_key).first()
            if existing is None:
                continue  # existed a moment ago, gone now (deleted by a failed/reclaimed attempt) — retry reservation
            if existing.completed_at is not None:
                return existing.result_summary, existing.affected_object_type, existing.affected_object_id
            if existing.reserved_at < now - timezone.timedelta(seconds=STALE_RESERVATION_SECONDS):
                AutomationActionSideEffect.objects.filter(pk=existing.pk, completed_at__isnull=True).delete()
                continue  # reclaimed an abandoned reservation — try again
            raise ReservationInProgress(
                f'Idempotency key {idempotency_key!r} is already reserved by an in-progress attempt.'
            )
    if effect is None:
        raise ReservationInProgress(f'Could not reserve idempotency key {idempotency_key!r}.')

    try:
        result, obj_type, obj_id = fn()
    except Exception:
        AutomationActionSideEffect.objects.filter(pk=effect.pk).delete()
        raise

    AutomationActionSideEffect.objects.filter(pk=effect.pk).update(
        result_summary=result, affected_object_type=obj_type or '', affected_object_id=obj_id or '',
        completed_at=timezone.now(),
    )
    return result, obj_type, obj_id


def reserve_action_slot(correlation_id, workspace_id, limit, reservation_key=None):
    """Atomically claim one of `limit` total action slots for this
    correlation_id. Returns True iff the caller may proceed to run an
    action; False means the hard cap is reached and nothing was consumed.

    Row-locked via select_for_update on AutomationCorrelationCounter so two
    genuinely concurrent workers each attempting to claim a slot near the
    boundary serialize on this row: at most `limit` claims can ever succeed
    for one correlation_id, regardless of how many workers race for them.
    Different correlation_ids never contend with each other (separate rows).

    reservation_key (optional but should always be passed for anything that
    can legitimately be retried, e.g. a ScheduledAction's id) makes the
    reservation itself idempotent: a second call with the SAME key finds its
    own already-reserved AutomationActionSlotReservation row and returns
    True without incrementing the counter again — a crash-point-B retry of
    an action that already reserved (and possibly already ran) its slot
    must not consume a second one.
    """
    for _attempt in range(2):
        try:
            with transaction.atomic():
                counter, _created = AutomationCorrelationCounter.objects.select_for_update().get_or_create(
                    correlation_id=correlation_id, defaults={'workspace_id': workspace_id},
                )
                if reservation_key and AutomationActionSlotReservation.objects.filter(
                    reservation_key=reservation_key,
                ).exists():
                    return True  # this exact attempt already holds a slot — idempotent re-entry, not a new claim
                if counter.actions_reserved >= limit:
                    return False
                if reservation_key:
                    AutomationActionSlotReservation.objects.create(
                        correlation_id=correlation_id, workspace_id=workspace_id, reservation_key=reservation_key,
                    )
                counter.actions_reserved += 1
                counter.save(update_fields=['actions_reserved', 'updated_at'])
                return True
        except IntegrityError:
            continue  # lost the create race to a concurrent first-reservation — retry, now get_or_create()/filter() see it
    return False
