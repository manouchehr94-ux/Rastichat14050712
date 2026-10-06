"""Trusted identity: turning a host-signed assertion into a RastiChat session, and managing the memberships an
integration owns. See docs/integrations/INTEGRATION_CONTRACT_V1.md §7-§8.

Invariants (each has a test):
* identity is established ONLY by a verified assertion or a signed server-to-server call — never by a browser field;
* an integration can only reach tenants it mapped itself (resolution is always by `integration`);
* staff users are dedicated accounts the integration created; an existing RastiChat user is never linked or taken over;
* an integration may only change or remove memberships it created (`ExternalMembership`);
* disabling is immediate: the account is deactivated, memberships removed, visitor sessions revoked; history is kept.
"""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from conversations.models import Conversation, Message
from platforms.models import PlatformMembership
from teams.models import TeamMembership
from visitors.models import Visitor, VisitorSession
from workspaces.models import WorkspaceMembership

from . import audit, scopes, tokens
from .errors import IntegrationAPIError
from .models import ExternalIdentity, ExternalMembership, IntegrationTenantMapping, external_id_validator

logger = logging.getLogger('integrations.security')

ACTOR_CUSTOMER = 'customer'
ACTOR_TENANT_STAFF = 'tenant_staff'
ACTOR_PLATFORM_STAFF = 'platform_staff'
ACTOR_SCOPE = {
    ACTOR_CUSTOMER: scopes.IDENTITY_CUSTOMER,
    ACTOR_TENANT_STAFF: scopes.IDENTITY_STAFF,
    ACTOR_PLATFORM_STAFF: scopes.IDENTITY_PLATFORM,
}
# Generic roles the host may assert -> RastiChat roles. Host-specific role names never reach RastiChat.
WORKSPACE_ROLES = {'owner': WorkspaceMembership.Roles.WORKSPACE_OWNER, 'admin': WorkspaceMembership.Roles.WORKSPACE_ADMIN,
                   'operator': WorkspaceMembership.Roles.WORKSPACE_OPERATOR}
PLATFORM_ROLES = {'owner': PlatformMembership.Roles.PLATFORM_OWNER, 'admin': PlatformMembership.Roles.PLATFORM_ADMIN,
                  'operator': PlatformMembership.Roles.PLATFORM_SUPPORT_AGENT}
VERIFIED_EXTERNAL_PREFIX = 'int:'   # Visitor.external_id of verified customers; the legacy unverified path may never claim it


def _deny(code, message, status=403):
    raise IntegrationAPIError(code, message, status)


def valid_external_id(value, field='sub'):
    try:
        if not isinstance(value, str):
            raise ValueError
        external_id_validator(value)
    except Exception:  # noqa: BLE001
        _deny('invalid_assertion', f'Invalid "{field}" claim.', 400)
    return value


# --------------------------------------------------------------------- assertion
class Assertion:
    """A verified, parsed identity assertion."""

    def __init__(self, verified):
        claims = verified.claims
        self.verified = verified
        self.integration = verified.integration
        self.key = verified.key
        self.actor = claims.get('actor')
        if self.actor not in ACTOR_SCOPE:
            _deny('invalid_assertion', 'Unknown actor.', 400)
        if ACTOR_SCOPE[self.actor] not in verified.scopes:
            _deny('scope_denied', 'The signing key is not permitted to assert this actor type.')
        self.sub = valid_external_id(claims.get('sub'))
        self.tenant = None
        if self.actor != ACTOR_PLATFORM_STAFF:
            self.tenant = valid_external_id(claims.get('tenant'), 'tenant')
        self.role = claims.get('role')
        if self.actor != ACTOR_CUSTOMER:
            table = PLATFORM_ROLES if self.actor == ACTOR_PLATFORM_STAFF else WORKSPACE_ROLES
            if self.role not in table:
                _deny('invalid_assertion', 'A valid generic "role" (owner|admin|operator) is required for staff.', 400)
        name = claims.get('name')
        self.name = name.strip()[:255] if isinstance(name, str) else ''
        self.origin = claims.get('origin')


def verify_assertion(raw):
    try:
        verified = tokens.verify_token(raw, purpose=tokens.PURPOSE_IDENTITY)
    except tokens.TokenError as exc:
        raise IntegrationAPIError(exc.code, exc.message, exc.status)
    return Assertion(verified)


def resolve_tenant(integration, external_tenant_id):
    """The ACTIVE tenant mapping of THIS integration, else 403 (uniform, no oracle for other integrations' ids)."""
    mapping = (IntegrationTenantMapping.objects.select_related('workspace', 'project')
               .filter(integration=integration, external_tenant_id=external_tenant_id).first())
    if mapping is None or mapping.status != IntegrationTenantMapping.Status.ACTIVE:
        _deny('tenant_unavailable', 'This tenant is not available.')
    return mapping


# ------------------------------------------------------------------------ staff
def ensure_staff_identity(integration, key, external_user_id, display_name=''):
    identity = (ExternalIdentity.objects.select_related('user')
                .filter(integration=integration, kind=ExternalIdentity.Kind.STAFF, external_user_id=external_user_id).first())
    if identity is None:
        try:
            with transaction.atomic():
                # dedicated account: synthetic email (never matched against real users), unusable password
                user = User.objects.create_user(email=f'ext-{uuid.uuid4().hex}@integration.invalid', password=None,
                                                display_name=display_name[:255])
                identity = ExternalIdentity.objects.create(
                    integration=integration, kind=ExternalIdentity.Kind.STAFF, external_user_id=external_user_id, user=user)
                audit.record('staff_identity_created', integration=integration, key=key,
                             target_type='external_identity', target_id=identity.pk)
        except IntegrityError:
            identity = ExternalIdentity.objects.select_related('user').get(
                integration=integration, kind=ExternalIdentity.Kind.STAFF, external_user_id=external_user_id)
    if identity.status == ExternalIdentity.Status.DISABLED:
        _deny('identity_disabled', 'This identity is disabled.')
    update = []
    if display_name and identity.user.display_name != display_name:
        identity.user.display_name = display_name
        update.append('display_name')
    if update:
        identity.user.save(update_fields=update)
    identity.last_asserted_at = timezone.now()
    identity.save(update_fields=['last_asserted_at'])
    return identity


@transaction.atomic
def set_workspace_membership(integration, key, identity, mapping, role):
    wm, created = WorkspaceMembership.objects.get_or_create(
        user=identity.user, workspace=mapping.workspace, defaults={'role': WORKSPACE_ROLES[role]})
    changed = created
    if not created and wm.role != WORKSPACE_ROLES[role]:
        wm.role = WORKSPACE_ROLES[role]
        wm.save(update_fields=['role'])
        changed = True
    em, em_created = ExternalMembership.objects.update_or_create(
        identity=identity, tenant_mapping=mapping, defaults={'external_role': role})
    if changed or em_created:
        audit.record('membership_set', integration=integration, key=key, target_type='external_membership',
                     target_id=em.pk, external_tenant_id=mapping.external_tenant_id, role=role)
    return wm


@transaction.atomic
def set_platform_membership(integration, key, identity, role):
    platform = integration.platform
    pm, created = PlatformMembership.objects.get_or_create(
        user=identity.user, platform=platform, defaults={'role': PLATFORM_ROLES[role]})
    changed = created
    if not created and pm.role != PLATFORM_ROLES[role]:
        pm.role = PLATFORM_ROLES[role]
        pm.save(update_fields=['role'])
        changed = True
    em, em_created = ExternalMembership.objects.update_or_create(
        identity=identity, platform=platform, defaults={'external_role': role})
    if changed or em_created:
        audit.record('platform_membership_set', integration=integration, key=key, target_type='external_membership',
                     target_id=em.pk, role=role)
    return pm


@transaction.atomic
def remove_membership(integration, key, identity, *, mapping=None, platform=None):
    """Remove a membership THIS integration created. Idempotent; returns True if something was removed."""
    em = ExternalMembership.objects.filter(identity=identity, tenant_mapping=mapping, platform=platform).first()
    if em is None:
        return False
    if mapping is not None:
        WorkspaceMembership.objects.filter(user=identity.user, workspace=mapping.workspace).delete()
        # team roles (incl. SUPERVISOR) outlive the workspace membership unless removed explicitly
        TeamMembership.objects.filter(user=identity.user, team__workspace=mapping.workspace).delete()
    else:
        PlatformMembership.objects.filter(user=identity.user, platform=platform).delete()
    em.delete()
    audit.record('membership_removed', integration=integration, key=key, target_type='external_identity',
                 target_id=identity.pk, external_tenant_id=mapping.external_tenant_id if mapping else None,
                 scope='tenant' if mapping else 'platform')
    return True


def issue_staff_token(user):
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=timedelta(minutes=settings.INTEGRATION_STAFF_SESSION_MINUTES))
    return str(token), settings.INTEGRATION_STAFF_SESSION_MINUTES * 60


@transaction.atomic
def disable_identity(integration, key, identity):
    """Stop everything this identity can do, now. History is kept."""
    if identity.status == ExternalIdentity.Status.DISABLED:
        return False
    identity.status = ExternalIdentity.Status.DISABLED
    identity.disabled_at = timezone.now()
    identity.save(update_fields=['status', 'disabled_at'])
    if identity.kind == ExternalIdentity.Kind.STAFF:
        for em in list(identity.memberships.select_related('tenant_mapping__workspace', 'platform')):
            remove_membership(integration, key, identity, mapping=em.tenant_mapping, platform=em.platform)
        identity.user.is_active = False
        identity.user.save(update_fields=['is_active'])
    else:
        VisitorSession.objects.filter(visitor=identity.visitor, revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit.record('identity_disabled', integration=integration, key=key, target_type='external_identity',
                 target_id=identity.pk, kind=identity.kind)
    return True


@transaction.atomic
def enable_identity(integration, key, identity):
    if identity.status == ExternalIdentity.Status.ACTIVE:
        return False
    identity.status = ExternalIdentity.Status.ACTIVE
    identity.disabled_at = None
    identity.save(update_fields=['status', 'disabled_at'])
    if identity.kind == ExternalIdentity.Kind.STAFF:
        identity.user.is_active = True
        identity.user.save(update_fields=['is_active'])
    audit.record('identity_enabled', integration=integration, key=key, target_type='external_identity',
                 target_id=identity.pk, kind=identity.kind)
    return True


# --------------------------------------------------------------------- customer
def customer_identity(integration, key, mapping, external_user_id, display_name=''):
    identity = (ExternalIdentity.objects.select_related('visitor')
                .filter(integration=integration, kind=ExternalIdentity.Kind.CUSTOMER,
                        tenant_mapping=mapping, external_user_id=external_user_id).first())
    if identity is None:
        try:
            with transaction.atomic():
                visitor = Visitor.objects.create(
                    project=mapping.project, external_id=f'{VERIFIED_EXTERNAL_PREFIX}{integration.slug}:{external_user_id}',
                    name=display_name or None)
                identity = ExternalIdentity.objects.create(
                    integration=integration, kind=ExternalIdentity.Kind.CUSTOMER, external_user_id=external_user_id,
                    tenant_mapping=mapping, visitor=visitor)
                audit.record('customer_identity_created', integration=integration, key=key,
                             target_type='external_identity', target_id=identity.pk)
        except IntegrityError:
            identity = ExternalIdentity.objects.select_related('visitor').get(
                integration=integration, kind=ExternalIdentity.Kind.CUSTOMER, tenant_mapping=mapping,
                external_user_id=external_user_id)
    if identity.status == ExternalIdentity.Status.DISABLED:
        _deny('identity_disabled', 'This identity is disabled.')
    if display_name and identity.visitor.name != display_name:
        identity.visitor.name = display_name
        identity.visitor.save(update_fields=['name', 'updated_at'])
    identity.last_asserted_at = timezone.now()
    identity.save(update_fields=['last_asserted_at'])
    return identity


@transaction.atomic
def upgrade_guest(integration, key, identity, guest_session):
    """Attach a guest's conversations to the verified visitor.

    Requires BOTH a valid assertion (who the person is) and the guest's own session credential (that this browser
    owns that guest history) — an external id alone never merges anything. Same project only. A guest's OPEN
    conversation moves only if the verified visitor has none open (never two open threads for one visitor; the
    other stays with the retired guest record, still visible to operators). The guest session is revoked.
    """
    guest = guest_session.visitor
    verified = identity.visitor
    if guest.pk == verified.pk or guest.project_id != verified.project_id or hasattr(guest, 'external_identity'):
        return 0
    has_open = Conversation.objects.filter(
        visitor=verified, type=Conversation.Type.CUSTOMER, status__in=Conversation.ACTIVE_STATUSES).exists()
    moved = 0
    for conv in Conversation.objects.select_for_update().filter(visitor=guest, type=Conversation.Type.CUSTOMER):
        if conv.status in Conversation.ACTIVE_STATUSES:
            if has_open:
                continue
            has_open = True
        conv.visitor = verified
        conv.save(update_fields=['visitor', 'updated_at'])
        Message.objects.filter(conversation=conv, sender_visitor=guest).update(sender_visitor=verified)
        moved += 1
    VisitorSession.objects.filter(visitor=guest, revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit.record('guest_upgraded', integration=integration, key=key, target_type='external_identity',
                 target_id=identity.pk, conversations_moved=moved)
    return moved
