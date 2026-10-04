import uuid
from django.conf import settings
from django.db import models
from workspaces.models import Workspace
from visitors.models import Visitor
from conversations.models import Conversation


class Tag(models.Model):
    """A workspace-owned label. Conversation-visible only to operators — never to visitors."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='tags')
    name = models.CharField(max_length=60)
    color = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('workspace', 'name')
        ordering = ['name']

    def __str__(self):
        return self.name


class ConversationTag(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='conversation_tags')
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name='conversation_tags')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('conversation', 'tag')


class Note(models.Model):
    """A discrete, authored, timestamped private operator note on a conversation.

    Replaces ad hoc use of the legacy free-text `Conversation.notes` field
    (kept for backward compatibility, but new UI should write here instead).
    Never exposed on any visitor-facing endpoint.
    """
    MAX_LENGTH = 4000

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='operator_notes')
    body = models.TextField(max_length=MAX_LENGTH)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class CustomerProfile(models.Model):
    """Local, tenant-scoped CRM-style extension of Visitor.

    This is deliberately a local, dev-mode data source (see
    docs/architecture/COMMERCE_INTEGRATION.md, "Option A"): fields here are
    operator-editable and there is a documented extension point for a
    future external adapter to populate/replace them. No tenant is assumed
    to have real commerce data — callers must handle an absent profile.
    """
    visitor = models.OneToOneField(Visitor, on_delete=models.CASCADE, related_name='profile')
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='customer_profiles')
    location = models.CharField(max_length=255, blank=True, default='')
    score = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    # Free-form operator-editable key/value data, read (never written) by
    # automation conditions via a whitelisted-key, safe dict lookup — not
    # arbitrary ORM/attribute traversal. See automations/conditions.py.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CustomerOrder(models.Model):
    """Local dev-mode order snapshot. See CustomerProfile docstring."""

    class Status(models.TextChoices):
        PROCESSING = 'PROCESSING', 'Processing'
        SHIPPED = 'SHIPPED', 'Shipped'
        DELIVERED = 'DELIVERED', 'Delivered'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='customer_orders')
    visitor = models.ForeignKey(Visitor, on_delete=models.CASCADE, related_name='orders')
    external_order_id = models.CharField(max_length=255, blank=True, default='')
    product_name = models.CharField(max_length=255)
    product_image = models.URLField(blank=True, default='')
    price = models.DecimalField(max_digits=12, decimal_places=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    ordered_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ordered_at']
