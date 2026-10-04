"""Idempotent, concurrency-safe periodic SLA evaluator.

Run on a schedule (cron, or later a Celery beat task — this command is
deliberately framework-agnostic so it drops into either) via:

    python manage.py evaluate_sla

Each ConversationSLA row is locked (`select_for_update()`) while it's
processed, and every state transition is guarded by a "have we already done
this" field (`*_breached_at`, `*_approaching_notified_at`) checked inside
that same locked transaction — so running the command twice in a row, or two
overlapping runs, never double-fires a breach/approaching notification.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from conversations.models import Conversation
from notifications.models import Notification
from notifications.services import notify
from sla.models import ConversationSLA

# "Approaching" = within this fraction of the target duration remaining, or
# within this fixed lead time, whichever is smaller.
APPROACHING_FRACTION = 0.2
APPROACHING_MAX_LEAD = timedelta(minutes=15)


def _is_approaching(due_at, target_minutes, now):
    if due_at is None or now >= due_at:
        return False
    lead = min(APPROACHING_MAX_LEAD, timedelta(minutes=target_minutes) * APPROACHING_FRACTION) if target_minutes else APPROACHING_MAX_LEAD
    return due_at - now <= lead


def _recipients(conv):
    if conv.assigned_to_id:
        return [conv.assigned_to]
    if conv.team_id:
        from teams.models import TeamMembership
        return [m.user for m in TeamMembership.objects.filter(team_id=conv.team_id, is_active=True).select_related('user')]
    return []


class Command(BaseCommand):
    help = 'Evaluate SLA deadlines: flag approaching/breached conversations, notify, escalate, and audit.'

    def handle(self, *args, **options):
        now = timezone.now()
        processed = 0
        breached = 0
        approaching = 0

        qs = ConversationSLA.objects.exclude(
            conversation__status__in=[Conversation.Status.CLOSED, Conversation.Status.RESOLVED],
        ).select_related('conversation')

        for sla_id in list(qs.values_list('id', flat=True)):
            with transaction.atomic():
                try:
                    sla = ConversationSLA.objects.select_for_update().select_related('conversation').get(pk=sla_id)
                except ConversationSLA.DoesNotExist:
                    continue
                conv = sla.conversation
                processed += 1
                changed_fields = []

                if not sla.first_responded_at:
                    b, a = self._evaluate_clock(
                        conv, sla, now, due_field='first_response_due_at', target_minutes=sla.first_response_target_minutes,
                        breach_field='first_response_breached_at', notify_field='first_response_approaching_notified_at',
                        clock_name='first response',
                    )
                    breached += b
                    approaching += a
                    if b or a:
                        changed_fields += ['first_response_breached_at', 'first_response_approaching_notified_at']

                if sla.next_response_due_at and not sla.resolved_at:
                    b, a = self._evaluate_clock(
                        conv, sla, now, due_field='next_response_due_at', target_minutes=sla.next_response_target_minutes,
                        breach_field='next_response_breached_at', notify_field='next_response_approaching_notified_at',
                        clock_name='next response',
                    )
                    breached += b
                    approaching += a
                    if b or a:
                        changed_fields += ['next_response_breached_at', 'next_response_approaching_notified_at']

                if not sla.resolved_at:
                    b, a = self._evaluate_clock(
                        conv, sla, now, due_field='resolution_due_at', target_minutes=sla.resolution_target_minutes,
                        breach_field='resolution_breached_at', notify_field='resolution_approaching_notified_at',
                        clock_name='resolution', escalate_on_breach=True,
                    )
                    breached += b
                    approaching += a
                    if b or a:
                        changed_fields += ['resolution_breached_at', 'resolution_approaching_notified_at']

                if changed_fields:
                    sla.save(update_fields=list(set(changed_fields)) + ['updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'Evaluated {processed} conversation(s): {breached} newly breached, {approaching} newly approaching.'
        ))

    def _evaluate_clock(self, conv, sla, now, due_field, target_minutes, breach_field, notify_field, clock_name,
                         escalate_on_breach=False):
        due_at = getattr(sla, due_field)
        if due_at is None:
            return 0, 0
        already_breached = getattr(sla, breach_field) is not None
        already_notified = getattr(sla, notify_field) is not None

        if now >= due_at:
            if already_breached:
                return 0, 0
            setattr(sla, breach_field, now)
            self._on_breach(conv, clock_name, escalate_on_breach)
            return 1, 0

        if not already_notified and _is_approaching(due_at, target_minutes, now):
            setattr(sla, notify_field, now)
            self._on_approaching(conv, clock_name, due_at)
            return 0, 1

        return 0, 0

    def _on_breach(self, conv, clock_name, escalate_on_breach):
        AuditEvent.objects.create(
            actor=None, action='sla_breached', target_type='conversation', target_id=str(conv.id),
            metadata={'clock': clock_name},
        )
        for user in _recipients(conv):
            notify(
                user, conv.workspace, Notification.EventType.SLA_BREACHED,
                f'SLA {clock_name} برای این گفتگو نقض شد', {'conversation_id': str(conv.id), 'clock': clock_name},
            )
        escalated = False
        if escalate_on_breach:
            from conversations.services import ConversationServiceError, escalate as escalate_conversation
            try:
                escalate_conversation(conv, actor=None, reason=f'SLA {clock_name} breach')
                escalated = True
            except ConversationServiceError:
                # No eligible (under-capacity) supervisor to escalate to —
                # one conversation's rejected escalation must never abort
                # the whole evaluator run; fall through to the same
                # priority-bump every other breach without an escalation
                # target already gets, below.
                pass
        if not escalated and conv.priority in (Conversation.Priority.LOW, Conversation.Priority.NORMAL):
            from conversations.services import set_priority
            from conversations.models import PriorityChange
            set_priority(
                conv, actor=None, priority=Conversation.Priority.HIGH,
                reason=f'SLA {clock_name} breach', reason_code=PriorityChange.Reason.SLA_BREACH,
            )
        self._broadcast(conv.id, 'conversation.sla_breached', {'conversation_id': str(conv.id), 'clock': clock_name})
        from automations.events import publish_event
        publish_event('SLA_BREACHED', conv.workspace_id, conversation_id=conv.id, actor_type='SYSTEM', payload={'clock': clock_name})

    def _on_approaching(self, conv, clock_name, due_at):
        AuditEvent.objects.create(
            actor=None, action='sla_approaching', target_type='conversation', target_id=str(conv.id),
            metadata={'clock': clock_name, 'due_at': due_at.isoformat()},
        )
        for user in _recipients(conv):
            notify(
                user, conv.workspace, Notification.EventType.SLA_APPROACHING,
                f'SLA {clock_name} برای این گفتگو در حال نزدیک شدن است', {'conversation_id': str(conv.id), 'clock': clock_name},
            )
        self._broadcast(conv.id, 'conversation.sla_approaching', {'conversation_id': str(conv.id), 'clock': clock_name})
        from automations.events import publish_event
        publish_event('SLA_APPROACHING', conv.workspace_id, conversation_id=conv.id, actor_type='SYSTEM', payload={'clock': clock_name})

    @staticmethod
    def _broadcast(conv_id, event_type, data):
        from conversations.services import broadcast_ops_event
        broadcast_ops_event(conv_id, event_type, data)
