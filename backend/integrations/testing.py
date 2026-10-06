"""Test helpers: a throw-away host (keypair + signer) so tests exercise the real verification path."""
import json
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from platforms.models import Platform

from .authentication import body_hash
from .keys import new_kid
from .models import Integration, IntegrationKey
from .scopes import (
    CONVERSATIONS_INITIATE, IDENTITY_CUSTOMER, IDENTITY_PLATFORM, IDENTITY_STAFF, TENANTS_READ, TENANTS_WRITE,
)

ALL_TEST_SCOPES = (TENANTS_READ, TENANTS_WRITE, IDENTITY_CUSTOMER, IDENTITY_STAFF, IDENTITY_PLATFORM, CONVERSATIONS_INITIATE)


class FakeHost:
    """A host application: owns a private key, registers the public half with RastiChat, signs requests."""

    def __init__(self, slug="fake-host", platform=None, scopes=ALL_TEST_SCOPES, key_scopes=None):
        self.private = Ed25519PrivateKey.generate()
        public_pem = self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.platform = platform or Platform.objects.create(name=f'Platform {slug}', external_id=f'platform-{slug}')
        self.integration = Integration.objects.create(
            slug=slug, name=slug.title(), platform=self.platform, scopes=list(scopes))
        self.key = IntegrationKey.objects.create(
            integration=self.integration, kid=new_kid(), public_key_pem=public_pem,
            scopes=list(key_scopes if key_scopes is not None else scopes))

    def token(self, method, path, body=b'', *, purpose='api', audience=None, issuer=None, kid=None, ttl=30,
              iat=None, jti=None, private=None, alg='EdDSA', extra=None, bind=True):
        now = int(iat if iat is not None else time.time())
        claims = {
            'iss': issuer or self.integration.slug, 'aud': audience or f'rastichat:{purpose}', 'sub': self.integration.slug,
            'iat': now, 'nbf': now - 1, 'exp': now + ttl, 'jti': jti or uuid.uuid4().hex,
        }
        if bind:
            claims.update({'htm': method, 'htu': path, 'bh': body_hash(body)})
        claims.update(extra or {})
        return jwt.encode(claims, private or self.private, algorithm=alg, headers={'kid': kid or self.key.kid})

    def call(self, client, method, path, payload=None, **token_kwargs):
        """Send a correctly signed request with a DRF/Django test client."""
        body = b'' if payload is None else json.dumps(payload).encode()
        extra_headers = token_kwargs.pop('extra_headers', None)
        token = self.token(method.upper(), path, body, **token_kwargs)
        kwargs = {'HTTP_AUTHORIZATION': f'Bearer {token}', **(extra_headers or {})}
        if payload is not None:
            kwargs.update(data=body, content_type='application/json')
        return getattr(client, method.lower())(path, **kwargs)

    def assertion(self, actor, sub, tenant=None, role=None, *, ttl=60, **claims):
        """A signed identity assertion (what the host backend hands to the browser)."""
        extra = {'actor': actor, 'sub': sub}
        if tenant is not None:
            extra['tenant'] = tenant
        if role is not None:
            extra['role'] = role
        extra.update(claims)
        sub_claim = extra.pop('sub')
        token_kwargs = {k: extra.pop(k) for k in list(extra) if k in ('audience', 'issuer', 'kid', 'iat', 'jti', 'private')}
        now = int(token_kwargs.pop('iat', time.time()))
        payload = {
            'iss': token_kwargs.get('issuer') or self.integration.slug, 'aud': token_kwargs.get('audience') or 'rastichat:identity',
            'sub': sub_claim, 'iat': now, 'exp': now + ttl, 'jti': token_kwargs.get('jti') or uuid.uuid4().hex, **extra,
        }
        return jwt.encode(payload, token_kwargs.get('private') or self.private, algorithm='EdDSA',
                          headers={'kid': token_kwargs.get('kid') or self.key.kid})
