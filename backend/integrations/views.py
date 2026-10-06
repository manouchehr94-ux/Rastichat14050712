"""Integration Contract v1 — server-to-server endpoints (`/api/v1/integrations/`)."""
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import provisioning, scopes
from .authentication import HasIntegrationScope, IntegrationAuthentication, IntegrationRateThrottle
from .base import CONTRACT_VERSION, ContractEnvelopeMixin
from .errors import IntegrationAPIError
from .models import external_id_validator
from .serializers import TenantUpsertSerializer


class IntegrationAPIView(ContractEnvelopeMixin, APIView):
    authentication_classes = [IntegrationAuthentication]
    permission_classes = [HasIntegrationScope]
    throttle_classes = [IntegrationRateThrottle]
    required_scopes = {}


class WhoAmIView(IntegrationAPIView):
    """`GET /api/v1/integrations/me/` — connectivity/authentication check; reveals nothing but the caller's own identity."""
    required_scopes = {'GET': (scopes.TENANTS_READ,)}

    def get(self, request):
        principal = request.user
        return Response({
            'integration': principal.integration.slug,
            'name': principal.integration.name,
            'kid': principal.key.kid,
            'scopes': sorted(principal.scopes),
            'contract': CONTRACT_VERSION,
        })


def _tenant_body(mapping, created=None):
    body = {
        'integration': mapping.integration.slug,
        'external_tenant_id': mapping.external_tenant_id,
        'status': mapping.status.lower(),
        'display_name': mapping.display_name,
        'verified_domains': mapping.verified_domains,
        'workspace_id': mapping.workspace_id,
        'project_id': mapping.project_id,
        'project_public_key': str(mapping.project.public_key),
        'metadata': mapping.metadata,
        'updated_at': mapping.updated_at,
    }
    if created is not None:
        body['created'] = created
    return body


class TenantView(IntegrationAPIView):
    """`PUT|GET|DELETE /api/v1/integrations/tenants/<external_tenant_id>/`.

    PUT is the idempotent "ensure tenant" operation: repeating it never creates a second workspace/project and only
    changes what the body explicitly supplies. DELETE archives (history retained)."""
    required_scopes = {
        'GET': (scopes.TENANTS_READ,),
        'PUT': (scopes.TENANTS_WRITE,),
        'DELETE': (scopes.TENANTS_WRITE,),
    }

    @staticmethod
    def _external_id(raw):
        try:
            external_id_validator(raw)
        except Exception:  # noqa: BLE001 - any validation failure is the same 400
            raise IntegrationAPIError('invalid_external_id', 'Invalid external tenant id.', 400)
        return raw

    def _mapping_or_404(self, request, external_tenant_id):
        mapping = provisioning.get_mapping(request.user.integration, external_tenant_id)
        if mapping is None:
            raise IntegrationAPIError('tenant_not_found', 'Tenant not found.', 404)
        return mapping

    def get(self, request, external_tenant_id):
        mapping = self._mapping_or_404(request, self._external_id(external_tenant_id))
        return Response(_tenant_body(mapping))

    def put(self, request, external_tenant_id):
        external_tenant_id = self._external_id(external_tenant_id)
        if not isinstance(request.data, dict):
            raise IntegrationAPIError('validation_error', 'A JSON object body is required.', 400)
        serializer = TenantUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        integration = request.user.integration
        if provisioning.get_mapping(integration, external_tenant_id) is None and not data.get('display_name'):
            raise ValidationError({'display_name': ['Required when creating a tenant.']})
        mapping, created = provisioning.ensure_tenant(integration, request.user.key, external_tenant_id, data)
        mapping.refresh_from_db()
        return Response(_tenant_body(mapping, created), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, external_tenant_id):
        mapping = self._mapping_or_404(request, self._external_id(external_tenant_id))
        mapping = provisioning.archive_tenant(request.user.integration, request.user.key, mapping)
        return Response(_tenant_body(mapping))
