import uuid
from django.db import models
from workspaces.models import Workspace
from .domains import validate_allowed_domains, normalize_allowed_domains_text

class Project(models.Model):
    name = models.CharField(max_length=255)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='projects')
    public_key = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    allowed_domains = models.TextField(
        blank=True, validators=[validate_allowed_domains],
        help_text=(
            "Hosts allowed to embed the widget, comma-separated: shop.example.com, shop.example.com:8443, *.example.com. "
            "Empty = not restricted per project (only the global CORS list applies)."
        ),
    )
    is_active = models.BooleanField(default=True)
    # Real store identity shown in the widget header — optional, the widget
    # must fall back gracefully (never invent a name/logo) when these are unset.
    logo_url = models.URLField(blank=True)
    subtitle = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.allowed_domains = normalize_allowed_domains_text(self.allowed_domains)
        super().save(*args, **kwargs)
        from .domains import clear_entries_cache
        clear_entries_cache()

    def __str__(self):
        return self.name
