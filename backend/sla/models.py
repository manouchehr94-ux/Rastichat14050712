import uuid
from django.conf import settings
from django.db import models
from workspaces.models import Workspace
from teams.models import Team
from queues.models import Queue
from conversations.models import Conversation


class BusinessCalendar(models.Model):
    """A workspace's operating calendar: timezone, weekly working windows,
    and date-specific exceptions (holidays / exceptional working days).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='business_calendars')
    name = models.CharField(max_length=255)
    # IANA timezone name (e.g. "Asia/Tehran") — never assume UTC as the
    # display/business timezone for deadline calculation.
    timezone = models.CharField(max_length=64, default='UTC')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'name')

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'


class WorkingInterval(models.Model):
    """One working window on one weekday (a calendar can have several per day)."""
    WEEKDAYS = [
        (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
        (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
    ]
    calendar = models.ForeignKey(BusinessCalendar, on_delete=models.CASCADE, related_name='working_intervals')
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ['weekday', 'start_time']


class Holiday(models.Model):
    """A calendar-date exception: either a closure (holiday) or, for a day
    that would otherwise be non-working, an exceptional working day.

    When `is_working` is True, `start_time`/`end_time` give that day its own
    one-off hours (e.g. a special Saturday shift); if left blank, the day
    falls back to whatever its normal weekday WorkingInterval would be.
    """
    calendar = models.ForeignKey(BusinessCalendar, on_delete=models.CASCADE, related_name='holidays')
    date = models.DateField()
    name = models.CharField(max_length=255, blank=True, default='')
    is_working = models.BooleanField(
        default=False, help_text='True = exceptional working day; False = closure/holiday.',
    )
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        unique_together = ('calendar', 'date')
        ordering = ['date']


class SLAPolicy(models.Model):
    """A workspace-scoped SLA policy. Applicability narrows by queue/team/priority
    — the most specific matching active policy wins when a conversation's SLA
    is applied (see sla/services.py:select_policy).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='sla_policies')
    name = models.CharField(max_length=255)
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, null=True, blank=True, related_name='sla_policies')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True, related_name='sla_policies')
    priority = models.CharField(
        max_length=10, choices=Conversation.Priority.choices, null=True, blank=True,
        help_text='Blank applies to every priority.',
    )
    calendar = models.ForeignKey(
        BusinessCalendar, on_delete=models.SET_NULL, null=True, blank=True, related_name='sla_policies',
        help_text='Blank means the targets are wall-clock (24/7), not business-hours-aware.',
    )
    first_response_target_minutes = models.PositiveIntegerField()
    next_response_target_minutes = models.PositiveIntegerField(null=True, blank=True)
    resolution_target_minutes = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'SLA policies'

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'

    def specificity(self):
        """Higher = more specific match, used to pick the best-fit policy
        when several active policies could apply to the same conversation.
        """
        return (
            (1 if self.queue_id else 0) + (1 if self.team_id else 0) + (1 if self.priority else 0)
        )


class ConversationSLA(models.Model):
    """The live SLA state for one conversation, including a frozen snapshot
    of the targets in effect when the policy was applied — later edits to the
    SLAPolicy itself never retroactively change a conversation already in flight.
    """
    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE, related_name='sla')
    policy = models.ForeignKey(SLAPolicy, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    policy_name_snapshot = models.CharField(max_length=255, blank=True, default='')
    first_response_target_minutes = models.PositiveIntegerField(null=True, blank=True)
    next_response_target_minutes = models.PositiveIntegerField(null=True, blank=True)
    resolution_target_minutes = models.PositiveIntegerField(null=True, blank=True)

    first_response_due_at = models.DateTimeField(null=True, blank=True)
    next_response_due_at = models.DateTimeField(null=True, blank=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True)

    first_responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    first_response_breached_at = models.DateTimeField(null=True, blank=True)
    next_response_breached_at = models.DateTimeField(null=True, blank=True)
    resolution_breached_at = models.DateTimeField(null=True, blank=True)

    # Dedup guards so the periodic evaluator never sends the same
    # approaching-deadline notification twice.
    first_response_approaching_notified_at = models.DateTimeField(null=True, blank=True)
    next_response_approaching_notified_at = models.DateTimeField(null=True, blank=True)
    resolution_approaching_notified_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
