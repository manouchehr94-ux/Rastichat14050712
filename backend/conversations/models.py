import uuid
from django.db import models
from django.conf import settings
from projects.models import Project
from workspaces.models import Workspace
from visitors.models import Visitor

class Conversation(models.Model):
    class Type(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer Support'
        PLATFORM_SUPPORT = 'PLATFORM_SUPPORT', 'Platform Support'

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        PENDING = 'PENDING', 'Pending'
        CLOSED = 'CLOSED', 'Closed'
        WAITING_FOR_WORKSPACE = 'WAITING_FOR_WORKSPACE', 'Waiting for Workspace'
        WAITING_FOR_PLATFORM = 'WAITING_FOR_PLATFORM', 'Waiting for Platform'
        RESOLVED = 'RESOLVED', 'Resolved'

    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        NORMAL = 'NORMAL', 'Normal'
        HIGH = 'HIGH', 'High'
        URGENT = 'URGENT', 'Urgent'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=20, choices=Type.choices)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN)

    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='conversations')
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='conversations')

    visitor = models.ForeignKey(Visitor, on_delete=models.SET_NULL, null=True, blank=True)

    subject = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=100, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)

    queue = models.ForeignKey(
        'queues.Queue', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations',
    )
    team = models.ForeignKey(
        'teams.Team', on_delete=models.SET_NULL, null=True, blank=True, related_name='conversations',
    )

    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_conversations')

    closed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    ACTIVE_STATUSES = (Status.OPEN, Status.PENDING, Status.WAITING_FOR_WORKSPACE, Status.WAITING_FOR_PLATFORM)


class PriorityChange(models.Model):
    class Reason(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual'
        QUEUE_DEFAULT = 'QUEUE_DEFAULT', 'Queue default'
        SLA_BREACH = 'SLA_BREACH', 'SLA breach'
        ESCALATION = 'ESCALATION', 'Escalation'

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='priority_changes')
    previous_priority = models.CharField(max_length=10, choices=Conversation.Priority.choices, null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Conversation.Priority.choices)
    reason_code = models.CharField(max_length=20, choices=Reason.choices, default=Reason.MANUAL)
    reason = models.TextField(blank=True, default='')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

class Message(models.Model):
    class SenderType(models.TextChoices):
        VISITOR = 'VISITOR', 'Visitor'
        USER = 'USER', 'User'
        SYSTEM = 'SYSTEM', 'System'

    class MessageType(models.TextChoices):
        TEXT = 'TEXT', 'Text'
        IMAGE = 'IMAGE', 'Image'
        VOICE = 'VOICE', 'Voice'
        PRODUCT = 'PRODUCT', 'Product'
        RATING_REQUEST = 'RATING_REQUEST', 'Rating Request'
        RATING = 'RATING', 'Rating'
        INTERNAL_NOTE = 'INTERNAL_NOTE', 'Internal Note'
        ARTICLE = 'ARTICLE', 'Knowledge Base Article'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=10, choices=SenderType.choices)
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    sender_visitor = models.ForeignKey(Visitor, on_delete=models.SET_NULL, null=True, blank=True)
    content = models.TextField(blank=True)
    message_type = models.CharField(max_length=20, choices=MessageType.choices, default=MessageType.TEXT)
    metadata = models.JSONField(default=dict, blank=True)
    attachment = models.FileField(upload_to='attachments/%Y/%m/%d/', null=True, blank=True)
    client_message_id = models.CharField(max_length=255, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('conversation', 'client_message_id')
        ordering = ['created_at']

class MessageReceipt(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='receipts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    visitor = models.ForeignKey(Visitor, on_delete=models.CASCADE, null=True, blank=True)
    read_at = models.DateTimeField(auto_now_add=True)

class Assignment(models.Model):
    """Append-only assignment/transfer history — a row is added for every
    action, existing rows are never edited, so the full history of a
    conversation's handling is always reconstructable.
    """
    class Action(models.TextChoices):
        ASSIGN = 'ASSIGN', 'Assign'
        REASSIGN = 'REASSIGN', 'Reassign'
        CLAIM = 'CLAIM', 'Claim'
        TRANSFER = 'TRANSFER', 'Transfer'
        UNASSIGN = 'UNASSIGN', 'Unassign'
        RETURN_TO_QUEUE = 'RETURN_TO_QUEUE', 'Return to queue'
        AUTO_ASSIGN = 'AUTO_ASSIGN', 'Automatic assignment'
        ESCALATE = 'ESCALATE', 'Escalate'

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='assignment_history')
    action = models.CharField(max_length=20, choices=Action.choices, default=Action.ASSIGN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assignments',
    )
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='assignments_made')
    previous_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    previous_team = models.ForeignKey('teams.Team', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    new_team = models.ForeignKey('teams.Team', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
