import uuid
from django.conf import settings
from django.db import models
from workspaces.models import Workspace
from teams.models import Team
from conversations.models import Message


class Mention(models.Model):
    """An @mention of a colleague inside an internal note.

    `note` always points at a Message with message_type=INTERNAL_NOTE — a
    Mention never exists on a customer-visible message. Creating one is what
    triggers the "agent mentioned" notification.
    """
    note = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='mentions')
    mentioned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mentions_received',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='mentions_made',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('note', 'mentioned_user')


class QuickReply(models.Model):
    class Scope(models.TextChoices):
        PERSONAL = 'PERSONAL', 'Personal'
        TEAM = 'TEAM', 'Team'
        WORKSPACE = 'WORKSPACE', 'Workspace'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='quick_replies')
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.PERSONAL)
    # Set only for PERSONAL (owner) or TEAM (team) scope; both blank for WORKSPACE scope.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='quick_replies',
    )
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True, related_name='quick_replies')
    title = models.CharField(max_length=255)
    body = models.TextField()
    shortcut = models.CharField(max_length=50, blank=True, default='')
    category = models.CharField(max_length=100, blank=True, default='')
    is_active = models.BooleanField(default=True)
    usage_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='quick_replies_created',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='quick_replies_updated',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-usage_count', 'title']
        verbose_name_plural = 'Quick replies'

    def __str__(self):
        return f'{self.title} ({self.scope})'
