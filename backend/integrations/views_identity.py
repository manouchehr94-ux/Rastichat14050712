"""Identity endpoints of Integration Contract v1.

Browser-facing exchanges (`/api/v1/identity/…`): the caller presents a host-signed assertion and receives a RastiChat
session. Server-to-server management (`/api/v1/integrations/…`): staff membership sync, deprovisioning.
"""
from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.serializers import UserSerializer
from projects.models import Project
from visitors.models import VisitorSession
from visitors.sessions import WidgetOriginNotAllowed, enforce_project_origin, get_valid_session, extract_session_token

from common import observability
from . import audit
from . import context as host_context
from . import identity as ident
from . import scopes
from .base import ContractEnvelopeMixin
from .errors import IntegrationAPIError
from .models import ExternalIdentity, IntegrationTenantMapping
from .views import IntegrationAPIView, TenantView


class _ExchangeView(ContractEnvelopeMixin, APIView):
    """Public (assertion-authenticated) endpoint: no session, no JWT — the signed assertion IS the credential."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'identity_exchange'

    @staticmethod
    def assertion_from(request):
        value = request.data.get('assertion') if hasattr(request.data, 'get') else None
        if not isinstance(value, str) or not value:
            raise ValidationError({'assertion': ['This field is required.']})
        return value


class CustomerExchangeView(_ExchangeView):
    """`POST /api/v1/identity/customer/ {project_key, assertion}` -> a visitor session for the VERIFIED customer."""

    def post(self, request):
        project_key = request.data.get('project_key') if hasattr(request.data, 'get') else None
        project = None
        try:
            project = Project.objects.select_related('workspace').filter(public_key=project_key, is_active=True).first()
        except Exception:  # noqa: BLE001 - a malformed uuid gets the same answer as an unknown project
            project = None
        if project is None or not project.workspace.is_active:
            raise IntegrationAPIError('invalid_project', 'Invalid or inactive project.', 400)
        try:
            enforce_project_origin(request, project, establishing=True)
        except WidgetOriginNotAllowed as exc:
            raise IntegrationAPIError(exc.detail['code'], exc.detail['error'], 403)

        assertion = ident.verify_assertion(self.assertion_from(request))
        if assertion.actor != ident.ACTOR_CUSTOMER:
            raise IntegrationAPIError('invalid_assertion', 'This endpoint accepts customer assertions only.', 400)
        mapping = getattr(project, 'integration_mapping', None)
        # the project must belong to exactly the tenant the host asserted — a valid assertion for tenant A is useless on B's project
        if (mapping is None or mapping.integration_id != assertion.integration.id
                or mapping.external_tenant_id != assertion.tenant
                or mapping.status != IntegrationTenantMapping.Status.ACTIVE):
            raise IntegrationAPIError('tenant_mismatch', 'The assertion is not valid for this project.', 403)
        if assertion.origin:
            origin = request.headers.get('Origin')
            if not origin or origin.rstrip('/') != str(assertion.origin).rstrip('/'):
                raise IntegrationAPIError('origin_mismatch', 'The assertion was issued for a different origin.', 403)

        identity = ident.customer_identity(assertion.integration, assertion.key, mapping, assertion.sub, assertion.name)
        if assertion.ctx is not None:   # optional host context riding in the signed assertion (same rules as the PUT API)
            profile, context = host_context.validate_snapshot(assertion.ctx)
            host_context.store(identity, assertion.key, profile, context)
        upgraded = 0
        guest_token = extract_session_token(request)
        if guest_token:
            guest = get_valid_session(guest_token, renew=False)
            if guest is not None:
                upgraded = ident.upgrade_guest(assertion.integration, assertion.key, identity, guest)
        session = VisitorSession.objects.create(visitor=identity.visitor)
        observability.emit('identity_exchange', label='customer', integration=assertion.integration.slug, upgraded=bool(upgraded))
        return Response({
            'visitor_id': str(identity.visitor_id),
            'session_token': str(session.token),
            'expires_at': session.expires_at,
            'identity': {'verified': True, 'conversations_attached': upgraded},
        }, status=status.HTTP_200_OK)


class StaffExchangeView(_ExchangeView):
    """`POST /api/v1/identity/staff/ {assertion}` -> dashboard access token for the staff member (tenant or platform)."""

    def post(self, request):
        assertion = ident.verify_assertion(self.assertion_from(request))
        if assertion.actor == ident.ACTOR_CUSTOMER:
            raise IntegrationAPIError('invalid_assertion', 'This endpoint accepts staff assertions only.', 400)
        mapping = None
        if assertion.actor == ident.ACTOR_TENANT_STAFF:
            mapping = ident.resolve_tenant(assertion.integration, assertion.tenant)
        identity = ident.ensure_staff_identity(assertion.integration, assertion.key, assertion.sub, assertion.name)
        if mapping is not None:
            ident.set_workspace_membership(assertion.integration, assertion.key, identity, mapping, assertion.role)
        else:
            ident.set_platform_membership(assertion.integration, assertion.key, identity, assertion.role)
        access, expires_in = ident.issue_staff_token(identity.user)
        observability.emit('identity_exchange', label=f'staff_{assertion.actor}', integration=assertion.integration.slug)
        user = identity.user
        return Response({
            'access': access, 'expires_in': expires_in,
            'user': UserSerializer(user).data,
            'workspace_id': mapping.workspace_id if mapping else None,
            'memberships': [{'workspace_id': m.workspace_id, 'role': m.role} for m in user.workspace_memberships.all()],
            'platform_roles': [m.role for m in user.platform_memberships.all()],
        })


# ------------------------------------------------------------ server-to-server management
class _MemberBase(IntegrationAPIView):
    @staticmethod
    def _role(request, table):
        role = request.data.get('role') if hasattr(request.data, 'get') else None
        if role not in table:
            raise ValidationError({'role': [f'Must be one of: {", ".join(table)}.']})
        return role

    @staticmethod
    def _name(request):
        name = request.data.get('display_name') if hasattr(request.data, 'get') else ''
        return name.strip()[:255] if isinstance(name, str) else ''


class TenantMemberView(_MemberBase):
    """`PUT|DELETE /api/v1/integrations/tenants/<t>/members/<user>/` — sync one staff member's role in one tenant
    (PUT creates the dedicated RastiChat account if needed; DELETE removes access, idempotently)."""
    required_scopes = {'PUT': (scopes.IDENTITY_STAFF,), 'DELETE': (scopes.IDENTITY_STAFF,)}

    def put(self, request, external_tenant_id, external_user_id):
        integration, key = request.user.integration, request.user.key
        role = self._role(request, ident.WORKSPACE_ROLES)
        mapping = ident.resolve_tenant(integration, TenantView._external_id(external_tenant_id))
        identity = ident.ensure_staff_identity(integration, key, ident.valid_external_id(external_user_id, 'user'),
                                               self._name(request))
        ident.set_workspace_membership(integration, key, identity, mapping, role)
        return Response({'external_user_id': external_user_id, 'external_tenant_id': external_tenant_id, 'role': role})

    def delete(self, request, external_tenant_id, external_user_id):
        integration, key = request.user.integration, request.user.key
        mapping = provisioning_mapping(integration, external_tenant_id)
        identity = _staff_identity(integration, external_user_id)
        removed = bool(identity) and ident.remove_membership(integration, key, identity, mapping=mapping)
        return Response({'removed': removed})


class PlatformMemberView(_MemberBase):
    """`PUT|DELETE /api/v1/integrations/platform/members/<user>/` — platform-level (support/owner) staff."""
    required_scopes = {'PUT': (scopes.IDENTITY_PLATFORM,), 'DELETE': (scopes.IDENTITY_PLATFORM,)}

    def put(self, request, external_user_id):
        integration, key = request.user.integration, request.user.key
        role = self._role(request, ident.PLATFORM_ROLES)
        identity = ident.ensure_staff_identity(integration, key, ident.valid_external_id(external_user_id, 'user'),
                                               self._name(request))
        ident.set_platform_membership(integration, key, identity, role)
        return Response({'external_user_id': external_user_id, 'role': role})

    def delete(self, request, external_user_id):
        integration, key = request.user.integration, request.user.key
        identity = _staff_identity(integration, external_user_id)
        removed = bool(identity) and ident.remove_membership(integration, key, identity, platform=integration.platform)
        return Response({'removed': removed})


class StaffIdentityStateView(IntegrationAPIView):
    """`POST /api/v1/integrations/users/<user>/disable|enable/` — host disabled/re-enabled the person."""
    required_scopes = {'POST': (scopes.IDENTITY_STAFF,)}
    action = None

    def post(self, request, external_user_id):
        integration, key = request.user.integration, request.user.key
        identity = _staff_identity(integration, external_user_id)
        if identity is None:
            raise IntegrationAPIError('identity_not_found', 'Unknown user.', 404)
        changed = (ident.disable_identity if self.action == 'disable' else ident.enable_identity)(integration, key, identity)
        return Response({'external_user_id': external_user_id, 'status': 'disabled' if self.action == 'disable' else 'active',
                         'changed': changed})


class ContextView(IntegrationAPIView):
    """`PUT|GET|DELETE /api/v1/integrations/tenants/<t>/contexts/<customer>/` — the host pushes a small, flat, tenant-scoped
    snapshot about one VERIFIED customer; operators see it in the conversation sidebar (Contract v1 §11).

    PUT replaces the whole snapshot (idempotent). Credentials / payment-looking data and nesting are refused. The customer
    identity is created on first push (it is the same identity the customer's later assertion resolves to)."""
    required_scopes = {m: (scopes.CONTEXT_WRITE,) for m in ('GET', 'PUT', 'DELETE')}

    def _identity(self, request, external_tenant_id, external_user_id, *, create):
        integration, key = request.user.integration, request.user.key
        ident.valid_external_id(external_user_id, 'customer')
        mapping = ident.resolve_tenant(integration, TenantView._external_id(external_tenant_id))
        if create:
            return mapping, ident.customer_identity(integration, key, mapping, external_user_id, touch=False)
        identity = ExternalIdentity.objects.select_related('visitor').filter(
            integration=integration, kind=ExternalIdentity.Kind.CUSTOMER, tenant_mapping=mapping,
            external_user_id=external_user_id).first()
        if identity is None:
            raise IntegrationAPIError('identity_not_found', 'Unknown customer.', 404)
        return mapping, identity

    @staticmethod
    def _body(row):
        return {'profile': row.profile, 'context': row.context, 'updated_at': row.updated_at}

    def put(self, request, external_tenant_id, external_user_id):
        profile, context = host_context.validate_snapshot(request.data if hasattr(request.data, 'get') else None)
        _, identity = self._identity(request, external_tenant_id, external_user_id, create=True)
        row = host_context.store(identity, request.user.key, profile, context)
        audit.record('context_updated', integration=request.user.integration, key=request.user.key,
                     target_type='external_identity', target_id=identity.pk,
                     profile_keys=sorted(profile), context_keys=sorted(context))   # key NAMES only, never values
        observability.emit('context_updated', integration=request.user.integration.slug)
        return Response({'external_user_id': external_user_id, **self._body(row)})

    def get(self, request, external_tenant_id, external_user_id):
        _, identity = self._identity(request, external_tenant_id, external_user_id, create=False)
        row = getattr(identity, 'host_context', None)
        if row is None:
            raise IntegrationAPIError('context_not_found', 'No context has been pushed for this customer.', 404)
        return Response({'external_user_id': external_user_id, **self._body(row)})

    def delete(self, request, external_tenant_id, external_user_id):
        _, identity = self._identity(request, external_tenant_id, external_user_id, create=False)
        row = getattr(identity, 'host_context', None)
        if row is not None:
            row.delete()
            audit.record('context_deleted', integration=request.user.integration, key=request.user.key,
                         target_type='external_identity', target_id=identity.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerIdentityStateView(IntegrationAPIView):
    """`POST /api/v1/integrations/tenants/<t>/customers/<user>/disable|enable/` — cut off / restore one customer."""
    required_scopes = {'POST': (scopes.IDENTITY_CUSTOMER,)}
    action = None

    def post(self, request, external_tenant_id, external_user_id):
        integration, key = request.user.integration, request.user.key
        mapping = provisioning_mapping(integration, external_tenant_id)
        identity = ExternalIdentity.objects.select_related('visitor').filter(
            integration=integration, kind=ExternalIdentity.Kind.CUSTOMER, tenant_mapping=mapping,
            external_user_id=external_user_id).first()
        if identity is None:
            raise IntegrationAPIError('identity_not_found', 'Unknown customer.', 404)
        changed = (ident.disable_identity if self.action == 'disable' else ident.enable_identity)(integration, key, identity)
        return Response({'external_user_id': external_user_id, 'status': 'disabled' if self.action == 'disable' else 'active',
                         'changed': changed})


def provisioning_mapping(integration, external_tenant_id):
    from . import provisioning
    mapping = provisioning.get_mapping(integration, TenantView._external_id(external_tenant_id))
    if mapping is None:
        raise IntegrationAPIError('tenant_not_found', 'Tenant not found.', 404)
    return mapping


def _staff_identity(integration, external_user_id):
    return ExternalIdentity.objects.select_related('user').filter(
        integration=integration, kind=ExternalIdentity.Kind.STAFF, external_user_id=external_user_id).first()


class SupportConversationView(IntegrationAPIView):
    """`POST /api/v1/integrations/tenants/<t>/support-conversations/` — a platform staff member of THIS integration opens
    (or resumes) a support conversation with one of its tenants, through the host's trusted backend (Contract v1 §12).

    Requires scope `conversations:initiate` and an `Idempotency-Key`. `initiator_user_id` must be a staff identity this
    integration created with an owner/admin platform membership (assertion or `PUT /platform/members/<id>/`); the
    message is authored by that RastiChat user, so replies and audit are attributable. One active thread per
    (tenant, `subject_key`): repeating the call resumes it.
    """
    required_scopes = {'POST': (scopes.CONVERSATIONS_INITIATE,)}

    def post(self, request, external_tenant_id):
        from conversations import support_service
        from conversations.models import Conversation
        from . import idempotency
        from .models import ExternalMembership
        integration = request.user.integration
        data = request.data if hasattr(request.data, 'get') else {}
        mapping = ident.resolve_tenant(integration, TenantView._external_id(external_tenant_id))
        initiator_id = ident.valid_external_id(data.get('initiator_user_id'), 'initiator_user_id')
        identity = _staff_identity(integration, initiator_id)
        membership = identity and ExternalMembership.objects.filter(
            identity=identity, platform=integration.platform, external_role__in=('owner', 'admin')).exists()
        if identity is None or identity.status != ExternalIdentity.Status.ACTIVE or not membership:
            raise IntegrationAPIError('initiator_not_authorized',
                                      'The initiator is not an active owner/admin of this integration\'s platform.', 403)
        payload = {'tenant': external_tenant_id, 'initiator': initiator_id, 'subject': data.get('subject'),
                   'subject_key': data.get('subject_key'), 'message': data.get('message'),
                   'client_message_id': data.get('client_message_id')}

        def perform():
            try:
                conv, created, _ = support_service.start_thread(
                    mapping.workspace, identity.user, Conversation.Side.PLATFORM, subject=data.get('subject'),
                    subject_key=data.get('subject_key'), message=data.get('message'),
                    client_message_id=data.get('client_message_id'))
            except support_service.SupportError as exc:
                raise IntegrationAPIError(exc.code, exc.message, exc.status)
            return (201 if created else 200), {
                'conversation_id': str(conv.id), 'created': created, 'status': conv.status, 'subject_key': conv.subject_key,
                'external_tenant_id': mapping.external_tenant_id, 'opened_by': conv.opened_by_side.lower()}

        return idempotency.run_once(request, integration, f'support-conversations:{external_tenant_id}', payload, perform)
