"""Generic integration layer: who may talk to RastiChat as a host application, and which external tenant maps to
which RastiChat workspace/project.

Nothing in here knows about any particular host (RastiSi or otherwise). A host is an `Integration`; its keys are
public keys only (RastiChat never stores a host's private key or a shared secret); its tenants are explicit,
unique `IntegrationTenantMapping` rows. Core chat models (Workspace, Project, Conversation, Visitor) carry no host
identifiers — the mapping table is the only place an external id lives.
"""
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from platforms.models import Platform
from projects.models import Project
from workspaces.models import Workspace

from .scopes import validate_scopes

slug_validator = RegexValidator(r'^[a-z0-9][a-z0-9-]{1,62}$', 'Lower-case letters, digits and dashes (2-63 chars).')
external_id_validator = RegexValidator(
    r'^[A-Za-z0-9][A-Za-z0-9._:~-]{0,254}$',
    'External ids are 1-255 chars of letters, digits and . _ : ~ - (no slashes or spaces).',
)


class Integration(models.Model):
    """An external application (host) that uses RastiChat through the Integration Contract."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # Also the `iss` claim of every token the host signs.
    slug = models.CharField(max_length=63, unique=True, validators=[slug_validator])
    integration_type = models.CharField(max_length=50, default='host_app')
    # Tenants of this integration become workspaces of this platform.
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, related_name='integrations')
    is_active = models.BooleanField(default=True)
    scopes = models.JSONField(default=list, blank=True, validators=[validate_scopes])
    # Integration-level, non-secret settings (reserved; validated by the slices that read them).
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.slug

    def clean(self):
        super().clean()
        validate_scopes(self.scopes)


class IntegrationKey(models.Model):
    """A public key the host signs tokens with (Ed25519 / JWS `EdDSA`). Only the public half is ever stored."""

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        REVOKED = 'REVOKED', 'Revoked'

    ALGORITHM = 'EdDSA'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    integration = models.ForeignKey(Integration, on_delete=models.CASCADE, related_name='keys')
    # Public identifier carried in the JWS `kid` header; globally unique.
    kid = models.CharField(max_length=64, unique=True)
    public_key_pem = models.TextField()
    scopes = models.JSONField(default=list, blank=True, validators=[validate_scopes])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    not_before = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True, default='')
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.kid

    def clean(self):
        super().clean()
        from .keys import load_public_key
        try:
            load_public_key(self.public_key_pem)
        except ValueError as exc:
            raise ValidationError({'public_key_pem': str(exc)})

    def is_usable(self, now=None):
        now = now or timezone.now()
        if self.status != self.Status.ACTIVE:
            return False
        if self.not_before and now < self.not_before:
            return False
        if self.expires_at and now >= self.expires_at:
            return False
        return True


class IntegrationTenantMapping(models.Model):
    """`integration + external tenant id` -> one RastiChat workspace and its default widget project.

    Explicit, unique and auditable. Deleting an integration or a mapping never cascades into chat data
    (PROTECT): archival is a status change, retention is a separate, deliberate operation.
    """

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        SUSPENDED = 'SUSPENDED', 'Suspended'
        ARCHIVED = 'ARCHIVED', 'Archived'

    integration = models.ForeignKey(Integration, on_delete=models.PROTECT, related_name='tenant_mappings')
    external_tenant_id = models.CharField(max_length=255, validators=[external_id_validator])
    workspace = models.OneToOneField(Workspace, on_delete=models.PROTECT, related_name='integration_mapping')
    project = models.OneToOneField(Project, on_delete=models.PROTECT, related_name='integration_mapping')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    # --- integration-managed fields (see docs/integrations/INTEGRATION_CONTRACT_V1.md §Ownership) ---
    display_name = models.CharField(max_length=255)
    verified_domains = models.JSONField(default=list, blank=True)
    # Entries this mapping itself added to Project.allowed_domains: the only ones a later sync may remove, so a
    # host can never delete a domain a RastiChat admin added by hand.
    synced_domains = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['integration', 'external_tenant_id'], name='uniq_integration_external_tenant'),
        ]

    def __str__(self):
        return f'{self.integration.slug}:{self.external_tenant_id}'
