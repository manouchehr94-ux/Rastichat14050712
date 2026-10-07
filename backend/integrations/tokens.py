"""Verification of integration-signed JWTs (JWS, Ed25519 / `EdDSA`).

One verifier serves every token a host signs — server-to-server API calls here, identity assertions in the SSO slice —
so the security-critical checks live in exactly one place:

* the algorithm is pinned to EdDSA and the key is a parsed Ed25519 *public* key, so algorithm-confusion
  (e.g. HS256 keyed with the public key) is impossible;
* `kid` selects a stored key; the key and its integration must be active, inside not-before/expiry;
* `iss` must equal the integration's slug, `aud` the purpose-specific audience (api vs identity tokens are not
  interchangeable), `exp`/`iat`/`jti` are mandatory, and `exp - iat` is capped per purpose (short TTL);
* every `jti` is single-use (Redis `SET NX`, fail-closed).

Nothing here logs a token or any claim value beyond identifiers.
"""
import logging
import time

import jwt
from django.conf import settings
from django.utils import timezone

from common import observability
from . import redis_store
from .keys import load_public_key
from .models import Integration, IntegrationKey

logger = logging.getLogger('integrations.security')

PURPOSE_API = 'api'
PURPOSE_IDENTITY = 'identity'


class TokenError(Exception):
    """A token was refused. `code` is stable and safe to return to the caller; `status` is the HTTP status."""

    def __init__(self, code, message, status=401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def audience_for(purpose):
    return f'{settings.INTEGRATION_TOKEN_AUDIENCE}:{purpose}'


def max_ttl_for(purpose):
    return settings.INTEGRATION_TOKEN_MAX_TTL_SECONDS[purpose]


class VerifiedToken:
    def __init__(self, integration, key, claims):
        self.integration = integration
        self.key = key
        self.claims = claims

    @property
    def scopes(self):
        """Scopes this token may exercise: key scopes ∩ integration scopes (least privilege)."""
        return set(self.key.scopes or []) & set(self.integration.scopes or [])


def _refuse(event, code, message, status=401, **extra):
    logger.warning(
        'integration_token_refused event=%s code=%s integration=%s kid=%s',
        event, code, extra.get('integration', '-'), extra.get('kid', '-'),
    )
    observability.emit('token_refused', level=logging.WARNING, label=code, reason=event, integration=extra.get('integration', '-'))
    raise TokenError(code, message, status)


def verify_token(token, *, purpose, consume=True):
    """Return a `VerifiedToken` or raise `TokenError`."""
    if not isinstance(token, str) or not token or len(token) > 8192:
        _refuse('malformed', 'invalid_token', 'Invalid token.')
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        _refuse('malformed', 'invalid_token', 'Invalid token.')
    kid = header.get('kid')
    if header.get('alg') != IntegrationKey.ALGORITHM or not isinstance(kid, str) or not kid:
        _refuse('bad_header', 'invalid_token', 'Invalid token.')

    key = IntegrationKey.objects.select_related('integration').filter(kid=kid).first()
    if key is None:
        _refuse('unknown_kid', 'invalid_token', 'Invalid token.')
    integration = key.integration
    if not integration.is_active:
        _refuse('integration_disabled', 'integration_disabled', 'This integration is disabled.', 403,
                integration=integration.slug)
    if not key.is_usable():
        _refuse('key_unusable', 'key_revoked', 'This signing key is revoked or not valid now.',
                integration=integration.slug, kid=kid)

    try:
        claims = jwt.decode(
            token, load_public_key(key.public_key_pem), algorithms=[IntegrationKey.ALGORITHM],
            audience=audience_for(purpose), issuer=integration.slug,
            leeway=settings.INTEGRATION_TOKEN_LEEWAY_SECONDS,
            options={'require': ['exp', 'iat', 'iss', 'aud', 'jti', 'sub']},
        )
    except jwt.ExpiredSignatureError:
        _refuse('expired', 'token_expired', 'Token expired.', integration=integration.slug, kid=kid)
    except jwt.InvalidAudienceError:
        _refuse('wrong_audience', 'invalid_token', 'Invalid token.', integration=integration.slug, kid=kid)
    except jwt.InvalidIssuerError:
        _refuse('wrong_issuer', 'invalid_token', 'Invalid token.', integration=integration.slug, kid=kid)
    except jwt.PyJWTError:
        _refuse('bad_signature_or_claims', 'invalid_token', 'Invalid token.', integration=integration.slug, kid=kid)

    iat, exp, jti = claims['iat'], claims['exp'], claims['jti']
    if not all(isinstance(v, (int, float)) for v in (iat, exp)) or not isinstance(jti, str) or not (8 <= len(jti) <= 128):
        _refuse('bad_claims', 'invalid_token', 'Invalid token.', integration=integration.slug, kid=kid)
    now = time.time()
    leeway = settings.INTEGRATION_TOKEN_LEEWAY_SECONDS
    if iat > now + leeway:
        _refuse('iat_in_future', 'invalid_token', 'Invalid token.', integration=integration.slug, kid=kid)
    if exp - iat > max_ttl_for(purpose):
        _refuse('ttl_too_long', 'invalid_token', 'Token lifetime exceeds the maximum.',
                integration=integration.slug, kid=kid)

    if consume:
        # Replay protection: a jti is single-use. TTL covers the token's whole possible life plus clock leeway,
        # so a replay can never outlive its record.
        ttl = (exp - now) + 2 * leeway + 1
        try:
            first_use = redis_store.claim_once(f'integ:jti:{integration.id}:{jti}', ttl)
        except redis_store.StoreUnavailable:
            logger.error('integration_replay_store_unavailable')
            raise TokenError('replay_store_unavailable', 'Service temporarily unavailable.', 503)
        if not first_use:
            _refuse('replayed', 'token_replayed', 'Token already used.', integration=integration.slug, kid=kid)

    _touch(key)
    return VerifiedToken(integration, key, claims)


def _touch(key):
    """Record `last_used_at` at most once a minute (visibility for rotation, not a write per request)."""
    now = timezone.now()
    if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60:
        IntegrationKey.objects.filter(pk=key.pk).update(last_used_at=now)
