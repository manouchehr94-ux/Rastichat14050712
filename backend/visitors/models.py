import uuid
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone
from projects.models import Project

class Visitor(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='visitors')
    external_id = models.CharField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    mobile = models.CharField(max_length=20, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Visitor {self.id}"

def default_session_expiry():
    """Expiry given to every newly created session (settings.VISITOR_SESSION_TTL_DAYS)."""
    return timezone.now() + timedelta(days=settings.VISITOR_SESSION_TTL_DAYS)


def default_session_hard_expiry():
    """Absolute lifetime limit (settings.VISITOR_SESSION_MAX_AGE_DAYS) — renewal never goes past it."""
    return timezone.now() + timedelta(days=settings.VISITOR_SESSION_MAX_AGE_DAYS)


class VisitorSession(models.Model):
    visitor = models.ForeignKey(Visitor, on_delete=models.CASCADE, related_name='sessions')
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Sliding expiry (see visitors/sessions.py). NULL only on rows that predate
    # this field; migration 0002 backfills them and the runtime treats NULL as
    # created_at + TTL, never as 'valid forever'.
    expires_at = models.DateTimeField(null=True, blank=True, default=default_session_expiry)
    # Absolute limit: sliding renewal and rotation never extend a session past it.
    hard_expires_at = models.DateTimeField(null=True, blank=True, default=default_session_hard_expiry)
    revoked_at = models.DateTimeField(null=True, blank=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return str(self.token)
