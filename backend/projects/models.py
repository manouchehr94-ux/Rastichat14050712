import uuid
from django.db import models
from workspaces.models import Workspace

class Project(models.Model):
    name = models.CharField(max_length=255)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='projects')
    public_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    allowed_domains = models.TextField(blank=True, help_text="Comma-separated list of allowed domains")
    is_active = models.BooleanField(default=True)
    # Real store identity shown in the widget header — optional, the widget
    # must fall back gracefully (never invent a name/logo) when these are unset.
    logo_url = models.URLField(blank=True)
    subtitle = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
