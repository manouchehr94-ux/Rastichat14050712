import uuid
from django.db import models
from workspaces.models import Workspace
from teams.models import Team


class Queue(models.Model):
    class Strategy(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual'
        ROUND_ROBIN = 'ROUND_ROBIN', 'Round Robin'
        LEAST_ACTIVE = 'LEAST_ACTIVE', 'Least Active'
        RANDOM = 'RANDOM', 'Random'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        NORMAL = 'NORMAL', 'Normal'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='queues')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='queues')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    # Lower number = evaluated first when more than one queue could claim an
    # incoming conversation (e.g. matching category across multiple queues).
    routing_priority = models.PositiveIntegerField(default=100)
    supported_categories = models.JSONField(default=list, blank=True)
    default_priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    assignment_strategy = models.CharField(max_length=20, choices=Strategy.choices, default=Strategy.MANUAL)
    max_active_per_agent = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Overrides an agent’s own capacity for this queue only, when set.',
    )
    fallback_queue = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='fallback_for',
    )
    # Deterministic round-robin cursor, persisted so restarts don't reset
    # fairness. Advanced under a row lock by the assignment service.
    round_robin_cursor = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'name')
        ordering = ['routing_priority', 'name']

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'
