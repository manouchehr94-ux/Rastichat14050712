"""Business-hours-aware SLA deadline calculation, policy selection, and the
per-conversation SLA lifecycle (apply / mark first response / mark resolved
/ recalculate next-response). The periodic breach evaluator (see
sla/management/commands/evaluate_sla.py) builds on top of this.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .models import SLAPolicy, ConversationSLA


def select_policy(conversation):
    """Pick the most specific active SLA policy applicable to this
    conversation (queue+team+priority beats team+priority beats priority
    beats a blanket workspace policy). Returns None when nothing applies —
    a conversation without a matching policy simply has no SLA tracked.
    """
    candidates = SLAPolicy.objects.filter(workspace=conversation.workspace, is_active=True)

    best = None
    best_score = -1
    for policy in candidates:
        if policy.queue_id and policy.queue_id != conversation.queue_id:
            continue
        if policy.team_id and policy.team_id != conversation.team_id:
            continue
        if policy.priority and policy.priority != conversation.priority:
            continue
        score = policy.specificity()
        if score > best_score:
            best = policy
            best_score = score
    return best


def _working_windows_for_date(calendar, date_):
    """Return a list of (start_datetime, end_datetime) tz-aware windows, in
    the calendar's own timezone, for the given calendar date. A Holiday row
    with is_working=False closes the day outright, regardless of its normal
    weekday schedule. A Holiday row with is_working=True is an exceptional
    working day: it uses its own start_time/end_time if given, otherwise
    falls back to whatever that weekday's normal WorkingInterval would be.
    """
    tz = ZoneInfo(calendar.timezone)
    holiday = calendar.holidays.filter(date=date_).first()
    if holiday and not holiday.is_working:
        return []
    if holiday and holiday.is_working and holiday.start_time and holiday.end_time:
        start = datetime.combine(date_, holiday.start_time, tzinfo=tz)
        end = datetime.combine(date_, holiday.end_time, tzinfo=tz)
        return [(start, end)] if end > start else []
    intervals = calendar.working_intervals.filter(weekday=date_.weekday())
    windows = []
    for interval in intervals:
        start = datetime.combine(date_, interval.start_time, tzinfo=tz)
        end = datetime.combine(date_, interval.end_time, tzinfo=tz)
        if end > start:
            windows.append((start, end))
    return windows


def add_business_minutes(start, minutes, calendar):
    """Return the datetime `minutes` of *business* time after `start`,
    respecting the calendar's weekly working windows and holidays. With no
    calendar, the target is wall-clock (24/7): just add the minutes.
    """
    if calendar is None:
        return start + timedelta(minutes=minutes)

    remaining = timedelta(minutes=minutes)
    cursor_date = start.astimezone(ZoneInfo(calendar.timezone)).date()
    cursor_instant = start
    # Bounded walk (~2 years of calendar days) so a misconfigured calendar
    # with no working intervals at all can never loop forever.
    for _ in range(365 * 2):
        for window_start, window_end in _working_windows_for_date(calendar, cursor_date):
            if window_end <= cursor_instant:
                continue
            effective_start = max(window_start, cursor_instant)
            available = window_end - effective_start
            if available <= timedelta(0):
                continue
            if available >= remaining:
                return effective_start + remaining
            remaining -= available
        cursor_date = cursor_date + timedelta(days=1)
        cursor_instant = datetime.combine(cursor_date, datetime.min.time(), tzinfo=ZoneInfo(calendar.timezone))
    # Fallback (should be unreachable for any sane calendar): don't hang forever.
    return start + timedelta(minutes=minutes)


@transaction.atomic
def apply_sla(conversation):
    """Select and apply the best-fit SLA policy to a conversation, snapshotting
    its targets and computing initial deadlines from `now`. Idempotent per
    conversation-lifecycle-entry-point: call again (e.g. after a transfer
    changes team) to re-select and reapply, which intentionally *does*
    recalculate — unlike a breach evaluator tick, this is a first-class
    lifecycle event (queue/team assignment), not background noise.
    """
    policy = select_policy(conversation)
    existing = ConversationSLA.objects.filter(conversation=conversation).first()
    if not policy:
        return existing

    now = timezone.now()
    sla, _ = ConversationSLA.objects.get_or_create(conversation=conversation)
    sla.policy = policy
    sla.policy_name_snapshot = policy.name
    sla.first_response_target_minutes = policy.first_response_target_minutes
    sla.next_response_target_minutes = policy.next_response_target_minutes
    sla.resolution_target_minutes = policy.resolution_target_minutes
    if not sla.first_responded_at:
        sla.first_response_due_at = add_business_minutes(now, policy.first_response_target_minutes, policy.calendar)
    if not sla.resolved_at:
        sla.resolution_due_at = add_business_minutes(now, policy.resolution_target_minutes, policy.calendar)
    sla.save()
    return sla


def mark_first_response(conversation):
    """An operator's first reply on this conversation — freezes the
    first-response deadline (no more approaching/breach checks needed for it).
    """
    sla = getattr(conversation, 'sla', None)
    if not sla or sla.first_responded_at:
        return sla
    sla.first_responded_at = timezone.now()
    sla.save(update_fields=['first_responded_at', 'updated_at'])
    return sla


def recalculate_next_response(conversation):
    """A new inbound visitor message after the first response starts (or
    restarts) the next-response clock — an explicit lifecycle event, not an
    unpredictable background recalculation.
    """
    sla = getattr(conversation, 'sla', None)
    if not sla or not sla.first_responded_at or not sla.next_response_target_minutes:
        return sla
    if sla.resolved_at:
        return sla
    policy = sla.policy
    calendar = policy.calendar if policy else None
    sla.next_response_due_at = add_business_minutes(timezone.now(), sla.next_response_target_minutes, calendar)
    sla.next_response_breached_at = None
    sla.next_response_approaching_notified_at = None
    sla.save(update_fields=[
        'next_response_due_at', 'next_response_breached_at', 'next_response_approaching_notified_at', 'updated_at',
    ])
    return sla


def clear_next_response(conversation):
    """An operator's reply satisfies the pending next-response clock."""
    sla = getattr(conversation, 'sla', None)
    if not sla or not sla.next_response_due_at:
        return sla
    sla.next_response_due_at = None
    sla.next_response_breached_at = None
    sla.save(update_fields=['next_response_due_at', 'next_response_breached_at', 'updated_at'])
    return sla


def mark_resolved(conversation):
    sla = getattr(conversation, 'sla', None)
    if not sla or sla.resolved_at:
        return sla
    sla.resolved_at = timezone.now()
    sla.save(update_fields=['resolved_at', 'updated_at'])
    return sla


def mark_reopened(conversation):
    """Reopening only recalculates the resolution clock — the historical
    first-response fields (already settled once, good or bad) are left
    untouched, matching "reopen recalculates only intended SLA fields".
    """
    sla = getattr(conversation, 'sla', None)
    if not sla:
        return sla
    sla.resolved_at = None
    sla.resolution_breached_at = None
    sla.resolution_approaching_notified_at = None
    if sla.resolution_target_minutes:
        calendar = sla.policy.calendar if sla.policy else None
        sla.resolution_due_at = add_business_minutes(timezone.now(), sla.resolution_target_minutes, calendar)
    sla.save(update_fields=[
        'resolved_at', 'resolution_breached_at', 'resolution_approaching_notified_at', 'resolution_due_at', 'updated_at',
    ])
    return sla
