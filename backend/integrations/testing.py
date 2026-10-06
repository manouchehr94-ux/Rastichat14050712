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
from .scopes import TENANTS_READ, TENANTS_WRITE


class FakeHost:
    """A host application: owns a private key, registers the public half with RastiChat, signs requests."""

    def __init__(self, slug='fake-host', platform=None, scopes=(TENANTS_READ, TENANTS_WRITE), key_scopes=None):
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
        token = self.token(method.upper(), path, body, **token_kwargs)
        kwargs = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        if payload is not None:
            kwargs.update(data=body, content_type='application/json')
        return getattr(client, method.lower())(path, **kwargs)
