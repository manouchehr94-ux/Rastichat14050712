import uuid
from django.conf import settings
from django.db import models
from workspaces.models import Workspace


class Team(models.Model):
    """A persistent support department/team (Sales, Shipping, Technical Support, ...).

    Tenant-scoped: every team belongs to exactly one workspace and its
    membership must never cross into another workspace's users.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='teams')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_teams',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'name')
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'


class TeamMembership(models.Model):
    class Role(models.TextChoices):
        MEMBER = 'MEMBER', 'Member'
        SUPERVISOR = 'SUPERVISOR', 'Supervisor'

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='team_memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')

    def __str__(self):
        return f'{self.user_id} @ {self.team_id} ({self.role})'
