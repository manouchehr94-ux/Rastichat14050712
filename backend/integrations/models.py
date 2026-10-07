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


class ExternalIdentity(models.Model):
    """A host's person, linked to the RastiChat identity that represents them.

    * CUSTOMER: scoped to one tenant — links to the (project-scoped) `Visitor`.
    * STAFF: scoped to the integration — links to a DEDICATED `accounts.User` created for this identity; staff
      access to tenants/the platform is carried by `ExternalMembership` rows.
    Never linked to a pre-existing RastiChat user (not even by matching email): an integration can only ever
    control identities it created itself.
    """

    class Kind(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer'
        STAFF = 'STAFF', 'Staff'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        DISABLED = 'DISABLED', 'Disabled'

    integration = models.ForeignKey(Integration, on_delete=models.PROTECT, related_name='identities')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    external_user_id = models.CharField(max_length=255)
    tenant_mapping = models.ForeignKey(
        IntegrationTenantMapping, on_delete=models.PROTECT, null=True, blank=True, related_name='identities')
    visitor = models.OneToOneField('visitors.Visitor', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='external_identity')
    user = models.OneToOneField('accounts.User', on_delete=models.PROTECT, null=True, blank=True,
                                related_name='external_identity')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    last_asserted_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['integration', 'tenant_mapping', 'external_user_id'], condition=models.Q(kind='CUSTOMER'),
                name='uniq_ext_customer_identity'),
            models.UniqueConstraint(
                fields=['integration', 'external_user_id'], condition=models.Q(kind='STAFF'),
                name='uniq_ext_staff_identity'),
            models.CheckConstraint(
                condition=(models.Q(kind='CUSTOMER', tenant_mapping__isnull=False, visitor__isnull=False)
                           | models.Q(kind='STAFF', tenant_mapping__isnull=True, user__isnull=False)),
                name='ext_identity_shape'),
        ]

    def __str__(self):
        return f'{self.integration.slug}:{self.kind}:{self.external_user_id}'


class ExternalMembership(models.Model):
    """A RastiChat membership created and owned by an integration (so it — and only it — may change or remove it).

    Exactly one of `tenant_mapping` (workspace membership) / `platform` (platform membership) is set.
    """
    identity = models.ForeignKey(ExternalIdentity, on_delete=models.CASCADE, related_name='memberships')
    tenant_mapping = models.ForeignKey(IntegrationTenantMapping, on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='external_memberships')
    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='external_memberships')
    # generic role the host asserted (owner|admin|operator) — never a host-specific role name
    external_role = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['identity', 'tenant_mapping'], condition=models.Q(tenant_mapping__isnull=False),
                                    name='uniq_ext_membership_tenant'),
            models.UniqueConstraint(fields=['identity', 'platform'], condition=models.Q(platform__isnull=False),
                                    name='uniq_ext_membership_platform'),
            models.CheckConstraint(
                condition=(models.Q(tenant_mapping__isnull=False, platform__isnull=True)
                           | models.Q(tenant_mapping__isnull=True, platform__isnull=False)),
                name='ext_membership_exactly_one_scope'),
        ]


class IdempotencyRecord(models.Model):
    """Stored result of a non-idempotent integration call made with an `Idempotency-Key` (Contract v1 §5): the same key
    with the same request replays the stored response; the same key with a different request is a conflict. Scoped per
    integration; purge with `manage.py integration_purge_idempotency`."""
    integration = models.ForeignKey(Integration, on_delete=models.CASCADE, related_name='idempotency_records')
    key = models.CharField(max_length=128)
    endpoint = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['integration', 'key'], name='uniq_integration_idempotency_key')]


class ExternalContext(models.Model):
    """Host-pushed, tenant-scoped snapshot about ONE verified customer (Contract v1 §11): small, flat, minimised, replaced as
    a whole on every push. RastiChat never reads the host's database — this is everything the operator gets.

    `profile`  = display data about the person (e.g. plan/tier, display locale);
    `context`  = what the conversation is about right now (e.g. current page, order reference).
    There is deliberately no "sensitive" section: sensitive data is not accepted at all in v1."""
    identity = models.OneToOneField(ExternalIdentity, on_delete=models.CASCADE, related_name='host_context')
    profile = models.JSONField(default=dict, blank=True)
    context = models.JSONField(default=dict, blank=True)
    source_kid = models.CharField(max_length=64, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)
