"""Idempotent tenant provisioning — the one place an external tenant becomes a RastiChat workspace + project.

Ownership rules (documented in docs/integrations/INTEGRATION_CONTRACT_V1.md):

* integration-managed, overwritten on every call that supplies them: external tenant id (immutable mapping key),
  `display_name` (workspace + default project name), `verified_domains` (merged into the project's
  `allowed_domains`; only entries this mapping added are ever removed), `status`, `metadata`;
* seed-only, applied when the tenant is first created and never again: `defaults.branding`;
* never touched: queues, teams, routing, canned replies, SLA, automations, memberships, and any domain a
  RastiChat admin added by hand.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEvent  # noqa: F401  (re-exported for tests that inspect the trail)
from projects.domains import parse_allowed_domains, clear_entries_cache
from projects.models import Project
from workspaces.models import Workspace

from . import audit
from .errors import TenantArchived
from .models import IntegrationTenantMapping

Status = IntegrationTenantMapping.Status
_STATUS_IN = {'active': Status.ACTIVE, 'suspended': Status.SUSPENDED}


def get_mapping(integration, external_tenant_id):
    """Tenant lookups are ALWAYS filtered by the calling integration — another integration's tenant is simply absent."""
    return (IntegrationTenantMapping.objects.select_related('workspace', 'project')
            .filter(integration=integration, external_tenant_id=external_tenant_id).first())


def ensure_tenant(integration, key, external_tenant_id, data):
    """Create-or-update. Returns (mapping, created). Safe to retry and to run concurrently."""
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return _ensure(integration, key, external_tenant_id, data)
        except IntegrityError:
            # A concurrent first-time provision won the unique constraint; the retry takes the update path.
            if attempt == 2:
                raise


def _ensure(integration, key, external_tenant_id, data):
    mapping = (IntegrationTenantMapping.objects.select_for_update()
               .select_related('workspace', 'project')
               .filter(integration=integration, external_tenant_id=external_tenant_id).first())
    if mapping is None:
        return _create(integration, key, external_tenant_id, data), True
    return _update(integration, key, mapping, data), False


def _create(integration, key, external_tenant_id, data):
    display_name = data['display_name']  # required on create (enforced by the view)
    branding = (data.get('defaults') or {}).get('branding') or {}
    workspace = Workspace.objects.create(name=display_name, platform=integration.platform, is_active=True)
    project = Project.objects.create(
        name=display_name, workspace=workspace,
        logo_url=branding.get('logo_url', ''), subtitle=branding.get('subtitle', ''),
    )
    status = _STATUS_IN[data['status']] if data.get('status') else Status.ACTIVE
    mapping = IntegrationTenantMapping.objects.create(
        integration=integration, external_tenant_id=external_tenant_id, workspace=workspace, project=project,
        status=status, display_name=display_name, metadata=data.get('metadata') or {},
        last_synced_at=timezone.now(),
    )
    if 'verified_domains' in data:
        _sync_domains(mapping, data['verified_domains'])
        mapping.save(update_fields=['verified_domains', 'synced_domains', 'updated_at'])
    if status != Status.ACTIVE:
        _apply_lifecycle(mapping)
    audit.record('tenant_provisioned', integration=integration, key=key, target_type='tenant_mapping',
                 target_id=mapping.pk, external_tenant_id=external_tenant_id, workspace_id=workspace.pk)
    return mapping


def _update(integration, key, mapping, data):
    changed = []
    want_status = _STATUS_IN[data['status']] if data.get('status') else None
    if mapping.status == Status.ARCHIVED and want_status is None:
        raise TenantArchived()

    name = data.get('display_name')
    if name is not None and name != mapping.display_name:
        mapping.display_name = name
        mapping.workspace.name = name
        mapping.workspace.save(update_fields=['name', 'updated_at'])
        mapping.project.name = name
        mapping.project.save(update_fields=['name', 'updated_at'])
        changed.append('display_name')
    if 'verified_domains' in data and _sync_domains(mapping, data['verified_domains']):
        changed.append('verified_domains')
    if 'metadata' in data and data['metadata'] != mapping.metadata:
        mapping.metadata = data['metadata']
        changed.append('metadata')
    if want_status is not None and want_status != mapping.status:
        previous = mapping.status
        mapping.status = want_status
        mapping.archived_at = None
        _apply_lifecycle(mapping)
        changed.append('status')
        audit.record('tenant_status_changed', integration=integration, key=key, target_type='tenant_mapping',
                     target_id=mapping.pk, external_tenant_id=mapping.external_tenant_id,
                     from_status=previous, to_status=want_status)
    mapping.last_synced_at = timezone.now()
    mapping.save()
    if changed:
        audit.record('tenant_updated', integration=integration, key=key, target_type='tenant_mapping',
                     target_id=mapping.pk, external_tenant_id=mapping.external_tenant_id, fields=changed)
    return mapping


def archive_tenant(integration, key, mapping):
    """Archive = stop serving the tenant; chat history is retained (retention is a separate, explicit operation)."""
    with transaction.atomic():
        mapping = (IntegrationTenantMapping.objects.select_for_update().select_related('workspace', 'project')
                   .get(pk=mapping.pk))
        if mapping.status == Status.ARCHIVED:
            return mapping
        previous = mapping.status
        mapping.status = Status.ARCHIVED
        mapping.archived_at = timezone.now()
        _apply_lifecycle(mapping)
        mapping.save()
        audit.record('tenant_status_changed', integration=integration, key=key, target_type='tenant_mapping',
                     target_id=mapping.pk, external_tenant_id=mapping.external_tenant_id,
                     from_status=previous, to_status=Status.ARCHIVED)
    return mapping


def _apply_lifecycle(mapping):
    """Suspended/archived tenants are inactive workspaces+projects, which the existing visitor-session, REST and
    WebSocket checks already treat as 'no access' (and which revokes live sessions on their next check)."""
    active = mapping.status == Status.ACTIVE
    mapping.workspace.is_active = active
    mapping.workspace.save(update_fields=['is_active', 'updated_at'])
    mapping.project.is_active = active
    mapping.project.save(update_fields=['is_active', 'updated_at'])
    if not active:
        _revoke_visitor_sessions(mapping.workspace)


def _revoke_visitor_sessions(workspace):
    from visitors.models import VisitorSession
    VisitorSession.objects.filter(visitor__project__workspace=workspace, revoked_at__isnull=True).update(
        revoked_at=timezone.now())


def _sync_domains(mapping, desired):
    """Merge `desired` into the project's allowed_domains. Adds what is missing (recording it as synced), removes
    only previously-synced entries that are no longer desired. Returns True if anything changed."""
    project = mapping.project
    current = parse_allowed_domains(project.allowed_domains)
    synced = list(mapping.synced_domains or [])
    new_current = list(current)
    new_synced = list(synced)

    for entry in synced:
        if entry not in desired and entry in new_current:
            new_current.remove(entry)
        if entry not in desired:
            new_synced.remove(entry)
    for entry in desired:
        if entry not in new_current:
            new_current.append(entry)
            if entry not in new_synced:
                new_synced.append(entry)

    changed = new_current != current or new_synced != synced or desired != mapping.verified_domains
    mapping.verified_domains = list(desired)
    mapping.synced_domains = new_synced
    if new_current != current:
        project.allowed_domains = ', '.join(new_current)
        project.save(update_fields=['allowed_domains', 'updated_at'])
        clear_entries_cache()
    return changed
