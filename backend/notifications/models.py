import uuid
from django.conf import settings
from django.db import models
from workspaces.models import Workspace


class Notification(models.Model):
    class EventType(models.TextChoices):
        CONVERSATION_ASSIGNED = 'CONVERSATION_ASSIGNED', 'Conversation assigned'
        CONVERSATION_TRANSFERRED = 'CONVERSATION_TRANSFERRED', 'Conversation transferred'
        MENTIONED = 'MENTIONED', 'Mentioned'
        CUSTOMER_REPLIED = 'CUSTOMER_REPLIED', 'Customer replied'
        SLA_APPROACHING = 'SLA_APPROACHING', 'SLA approaching'
        SLA_BREACHED = 'SLA_BREACHED', 'SLA breached'
        ESCALATION_RECEIVED = 'ESCALATION_RECEIVED', 'Escalation received'
        AUTOMATION_TRIGGERED = 'AUTOMATION_TRIGGERED', 'Automation notification'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications',
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='notifications')
    event_type = models.CharField(max_length=40, choices=EventType.choices)
    title = models.CharField(max_length=255)
    # Small, JSON-safe context only (conversation_id, actor name, ...) — never
    # secrets, tokens, or raw uploaded file contents.
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
