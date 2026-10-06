"""DRF authentication, permission and throttle classes for the server-to-server integration API.

A request carries `Authorization: Bearer <jwt>` where the JWT is signed by one of the host's registered Ed25519 keys
and is bound to exactly this request: `htm` (method), `htu` (path) and `bh` (base64url SHA-256 of the body). A token
therefore cannot be replayed (single-use `jti`), re-targeted at another endpoint, or paired with a different body.
"""
import base64
import hashlib
import hmac
import logging

from django.conf import settings
from rest_framework import permissions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from common import observability
from . import redis_store, tokens
from .errors import IntegrationAPIError

logger = logging.getLogger('integrations.security')


class IntegrationPrincipal:
    """What `request.user` is on an integration-API request. Deliberately NOT a RastiChat User."""
    is_authenticated = True
    is_anonymous = False
    is_active = True

    def __init__(self, verified):
        self.integration = verified.integration
        self.key = verified.key
        self.scopes = verified.scopes
        self.claims = verified.claims

    def __str__(self):
        return f'integration:{self.integration.slug}'


def body_hash(body: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(body or b'').digest()).rstrip(b'=').decode()


class IntegrationAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer realm="rastichat-integration"'

    def authenticate(self, request):
        ip = BaseThrottle().get_ident(request)
        fail_key = f'integ:authfail:{ip}'
        limit = settings.INTEGRATION_AUTH_FAILURES_PER_MINUTE
        try:
            if limit and redis_store.peek(fail_key) >= limit:
                raise IntegrationAPIError('rate_limited', 'Too many failed authentication attempts.', 429)
        except redis_store.StoreUnavailable:
            pass  # token verification below fails closed on its own; do not add a second failure mode here

        parts = get_authorization_header(request).split()
        if len(parts) != 2 or parts[0].lower() != b'bearer':
            self._failed(fail_key)
            raise IntegrationAPIError('missing_token', 'Authorization: Bearer <token> is required.', 401)
        try:
            verified = tokens.verify_token(parts[1].decode('ascii', 'ignore'), purpose=tokens.PURPOSE_API)
            self._check_binding(request, verified.claims)
        except tokens.TokenError as exc:
            if exc.status != 503:
                self._failed(fail_key)
            raise IntegrationAPIError(exc.code, exc.message, exc.status)
        return IntegrationPrincipal(verified), verified

    @staticmethod
    def _failed(fail_key):
        try:
            redis_store.hit(fail_key, 60)
        except redis_store.StoreUnavailable:
            pass

    @staticmethod
    def _check_binding(request, claims):
        django_request = request._request
        expected = (django_request.method, django_request.path, body_hash(django_request.body))
        got = (claims.get('htm'), claims.get('htu'), claims.get('bh'))
        if not all(isinstance(g, str) for g in got) or not all(
            hmac.compare_digest(e.encode(), g.encode()) for e, g in zip(expected, got)
        ):
            logger.warning('integration_token_refused event=binding_mismatch code=binding_mismatch integration=%s kid=-',
                           claims.get('iss', '-'))
            observability.emit('token_refused', level=logging.WARNING, label='binding_mismatch', reason='binding_mismatch')
            raise tokens.TokenError('binding_mismatch', 'Token is not bound to this request (htm/htu/bh).', 401)


class HasIntegrationScope(permissions.BasePermission):
    """The view declares `required_scopes = {'GET': ('tenants:read',), 'PUT': ('tenants:write',)}`; the principal's
    effective scopes (key ∩ integration) must include every scope listed for the request method."""

    def has_permission(self, request, view):
        principal = request.user
        if not isinstance(principal, IntegrationPrincipal):
            return False
        required = set(getattr(view, 'required_scopes', {}).get(request.method, ()))
        if not required:
            # fail closed: a method with no declared scope is not reachable
            return False
        if not required <= principal.scopes:
            logger.warning('integration_scope_denied integration=%s kid=%s', principal.integration.slug, principal.key.kid)
            observability.emit('scope_denied', integration=principal.integration.slug)
            raise IntegrationAPIError('scope_denied', 'The signing key is not permitted to do this.', 403)
        return True


class IntegrationRateThrottle(SimpleRateThrottle):
    """Per-integration request budget (settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['integration_api'])."""
    scope = 'integration_api'

    def get_cache_key(self, request, view):
        principal = request.user
        if not isinstance(principal, IntegrationPrincipal):
            return None
        return self.cache_format % {'scope': self.scope, 'ident': str(principal.integration.id)}
