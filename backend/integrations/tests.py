import json
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from conversations.models import Conversation
from platforms.models import Platform
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from workspaces.models import Workspace

from . import redis_store
from .models import Integration, IntegrationKey, IntegrationTenantMapping
from .testing import FakeHost

URL = '/api/v1/integrations/tenants/{}/'


def flush_redis_state():
    r = redis_store.get_redis()
    for key in r.scan_iter('integ:*'):
        r.delete(key)


class IntegrationTestBase(TestCase):
    def setUp(self):
        flush_redis_state()
        self.client = APIClient()
        self.host = FakeHost('acme')

    def put(self, ext_id, payload, host=None, **kw):
        return (host or self.host).call(self.client, 'put', URL.format(ext_id), payload, **kw)

    def error_code(self, response):
        return response.json()['error']['code']


class AuthenticationTests(IntegrationTestBase):
    def test_valid_signed_request_is_accepted(self):
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['integration'], 'acme')
        self.assertEqual(res['X-RastiChat-Contract'], 'integration-v1')

    def test_missing_and_garbage_tokens_are_refused(self):
        self.assertEqual(self.client.get('/api/v1/integrations/me/').status_code, 401)
        res = self.client.get('/api/v1/integrations/me/', HTTP_AUTHORIZATION='Bearer not-a-jwt')
        self.assertEqual((res.status_code, self.error_code(res)), (401, 'invalid_token'))

    def test_a_dashboard_user_jwt_is_not_an_integration_credential(self):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.tokens import RefreshToken
        user = get_user_model().objects.create_user(email='u@x.com', password='pass1234')
        access = str(RefreshToken.for_user(user).access_token)
        res = self.client.get('/api/v1/integrations/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(res.status_code, 401)

    def test_expired_token(self):
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', iat=1_000_000_000, ttl=30)
        self.assertEqual(self.error_code(res), 'token_expired')

    def test_ttl_longer_than_maximum_is_refused(self):
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', ttl=3600)
        self.assertEqual(res.status_code, 401)

    def test_replayed_token_is_refused(self):
        path = '/api/v1/integrations/me/'
        token = self.host.token('GET', path)
        first = self.client.get(path, HTTP_AUTHORIZATION=f'Bearer {token}')
        second = self.client.get(path, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(first.status_code, 200)
        self.assertEqual((second.status_code, self.error_code(second)), (401, 'token_replayed'))

    def test_wrong_audience_and_wrong_purpose(self):
        for kwargs in ({'audience': 'other:api'}, {'purpose': 'identity'}):
            res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', **kwargs)
            self.assertEqual((res.status_code, self.error_code(res)), (401, 'invalid_token'), kwargs)

    def test_wrong_issuer(self):
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', issuer='someone-else')
        self.assertEqual(self.error_code(res), 'invalid_token')

    def test_unknown_kid_and_foreign_signature(self):
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', kid='ick_doesnotexist')
        self.assertEqual(self.error_code(res), 'invalid_token')
        other = FakeHost('other')
        # signed with another host's private key but claiming acme's kid and issuer
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', private=other.private)
        self.assertEqual(self.error_code(res), 'invalid_token')

    def test_symmetric_algorithm_confusion_is_refused(self):
        """Classic attack: sign with HS256 using the PUBLIC key as the HMAC secret (built by hand: PyJWT itself
        refuses to do this)."""
        import base64
        import hashlib
        import hmac

        def b64(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
        now = int(timezone.now().timestamp())
        header = b64(json.dumps({'alg': 'HS256', 'kid': self.host.key.kid}).encode())
        payload = b64(json.dumps({'iss': 'acme', 'aud': 'rastichat:api', 'sub': 'acme', 'iat': now, 'exp': now + 30,
                                  'jti': 'a' * 16}).encode())
        signature = b64(hmac.new(self.host.key.public_key_pem.encode(), f'{header}.{payload}'.encode(),
                                 hashlib.sha256).digest())
        res = self.client.get('/api/v1/integrations/me/', HTTP_AUTHORIZATION=f'Bearer {header}.{payload}.{signature}')
        self.assertEqual((res.status_code, self.error_code(res)), (401, 'invalid_token'))

    def test_alg_none_is_refused(self):
        import jwt
        forged = jwt.encode({'iss': 'acme', 'aud': 'rastichat:api'}, None, algorithm='none',
                            headers={'kid': self.host.key.kid})
        res = self.client.get('/api/v1/integrations/me/', HTTP_AUTHORIZATION=f'Bearer {forged}')
        self.assertEqual(res.status_code, 401)

    def test_token_is_bound_to_method_path_and_body(self):
        path = URL.format('t1')
        body = json.dumps({'display_name': 'Shop'}).encode()
        token = self.host.token('PUT', path, body)
        # same token, different body
        res = self.client.put(path, data=json.dumps({'display_name': 'Evil'}).encode(),
                              content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual((res.status_code, self.error_code(res)), (401, 'binding_mismatch'))
        # different path
        token = self.host.token('PUT', URL.format('t2'), body)
        res = self.client.put(path, data=body, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.error_code(res), 'binding_mismatch')
        # different method
        token = self.host.token('GET', path, body)
        res = self.client.put(path, data=body, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.error_code(res), 'binding_mismatch')
        self.assertFalse(IntegrationTenantMapping.objects.exists())

    def test_unbound_token_is_refused(self):
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/', bind=False)
        self.assertEqual(self.error_code(res), 'binding_mismatch')

    def test_disabled_integration(self):
        self.host.integration.is_active = False
        self.host.integration.save()
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/')
        self.assertEqual((res.status_code, self.error_code(res)), (403, 'integration_disabled'))

    def test_revoked_and_expired_and_not_yet_valid_keys(self):
        key = self.host.key
        key.status = IntegrationKey.Status.REVOKED
        key.save()
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/')
        self.assertEqual((res.status_code, self.error_code(res)), (401, 'key_revoked'))
        key.status = IntegrationKey.Status.ACTIVE
        key.expires_at = timezone.now() - timedelta(seconds=1)
        key.save()
        self.assertEqual(self.host.call(self.client, 'get', '/api/v1/integrations/me/').status_code, 401)
        key.expires_at = None
        key.not_before = timezone.now() + timedelta(hours=1)
        key.save()
        self.assertEqual(self.host.call(self.client, 'get', '/api/v1/integrations/me/').status_code, 401)

    def test_key_rotation_overlap_and_revocation(self):
        second = FakeHost.__new__(FakeHost)  # reuse the same integration, new keypair
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from .keys import new_kid
        new_private = Ed25519PrivateKey.generate()
        new_key = IntegrationKey.objects.create(
            integration=self.host.integration, kid=new_kid(), scopes=self.host.integration.scopes,
            public_key_pem=new_private.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
        # both keys work during the overlap
        self.assertEqual(self.host.call(self.client, 'get', '/api/v1/integrations/me/').status_code, 200)
        self.assertEqual(self.host.call(self.client, 'get', '/api/v1/integrations/me/', kid=new_key.kid,
                                        private=new_private).status_code, 200)
        call_command('integration_key_revoke', kid=self.host.key.kid, reason='rotated')
        self.assertEqual(self.host.call(self.client, 'get', '/api/v1/integrations/me/').status_code, 401)
        self.assertEqual(self.host.call(self.client, 'get', '/api/v1/integrations/me/', kid=new_key.kid,
                                        private=new_private).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action='integration.key_revoked').exists())
        del second

    def test_replay_store_down_fails_closed(self):
        import redis
        with mock.patch.object(redis_store, 'claim_once', side_effect=redis_store.StoreUnavailable('down')):
            res = self.host.call(self.client, 'get', '/api/v1/integrations/me/')
        self.assertEqual((res.status_code, self.error_code(res)), (503, 'replay_store_unavailable'))
        del redis

    @override_settings(INTEGRATION_AUTH_FAILURES_PER_MINUTE=3)
    def test_failed_authentications_are_rate_limited_per_ip(self):
        for _ in range(3):
            self.assertEqual(self.client.get('/api/v1/integrations/me/', HTTP_AUTHORIZATION='Bearer x').status_code, 401)
        res = self.host.call(self.client, 'get', '/api/v1/integrations/me/')  # even a valid token is now throttled
        self.assertEqual((res.status_code, self.error_code(res)), (429, 'rate_limited'))


class ScopeTests(IntegrationTestBase):
    def test_key_scope_is_the_intersection(self):
        host = FakeHost('readonly', scopes=('tenants:read', 'tenants:write'), key_scopes=('tenants:read',))
        res = host.call(self.client, 'put', URL.format('t1'), {'display_name': 'X'})
        self.assertEqual((res.status_code, self.error_code(res)), (403, 'scope_denied'))
        self.assertEqual(host.call(self.client, 'get', '/api/v1/integrations/me/').status_code, 200)

    def test_key_cannot_exceed_integration_scopes(self):
        host = FakeHost('narrow', scopes=('tenants:read',), key_scopes=('tenants:read', 'tenants:write'))
        res = host.call(self.client, 'put', URL.format('t1'), {'display_name': 'X'})
        self.assertEqual(res.status_code, 403)


class ProvisioningTests(IntegrationTestBase):
    def test_create_then_repeat_is_idempotent(self):
        res = self.put('shop-1', {'display_name': 'Shop One', 'verified_domains': ['shop1.example.com']})
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body['created'])
        again = self.put('shop-1', {'display_name': 'Shop One', 'verified_domains': ['shop1.example.com']})
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()['created'])
        for field in ('workspace_id', 'project_id', 'project_public_key'):
            self.assertEqual(body[field], again.json()[field])
        self.assertEqual(Workspace.objects.filter(platform=self.host.platform).count(), 1)
        self.assertEqual(IntegrationTenantMapping.objects.count(), 1)
        self.assertEqual(Project.objects.get().allowed_domains, 'shop1.example.com')
        self.assertEqual(AuditEvent.objects.filter(action='integration.tenant_provisioned').count(), 1)
        self.assertFalse(AuditEvent.objects.filter(action='integration.tenant_updated').exists())

    def test_get_returns_the_mapping(self):
        self.put('shop-1', {'display_name': 'Shop One'})
        res = self.host.call(self.client, 'get', URL.format('shop-1'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['display_name'], 'Shop One')

    def test_create_requires_display_name(self):
        res = self.put('shop-1', {'verified_domains': ['a.example.com']})
        self.assertEqual((res.status_code, self.error_code(res)), (400, 'validation_error'))
        self.assertFalse(Workspace.objects.exists())

    def test_unknown_fields_are_rejected(self):
        res = self.put('shop-1', {'display_name': 'X', 'is_superuser': True})
        self.assertEqual(res.status_code, 400)
        res = self.put('shop-1', {'display_name': 'X', 'defaults': {'routing': {}}})
        self.assertEqual(res.status_code, 400)

    def test_secretish_metadata_rejected(self):
        res = self.put('shop-1', {'display_name': 'X', 'metadata': {'api_key': 'abc'}})
        self.assertEqual(res.status_code, 400)
        res = self.put('shop-1', {'display_name': 'X', 'metadata': {'plan': 'pro', 'seats': 3}})
        self.assertEqual(res.status_code, 201)

    def test_invalid_external_id(self):
        res = self.host.call(self.client, 'put', '/api/v1/integrations/tenants/bad id/', {'display_name': 'X'})
        self.assertIn(res.status_code, (400, 404))
        self.assertFalse(IntegrationTenantMapping.objects.exists())

    def test_wildcard_and_invalid_domains_rejected(self):
        for bad in ('*.example.com', 'not a domain', 'https://x.com/path'):
            res = self.put('shop-1', {'display_name': 'X', 'verified_domains': [bad]})
            self.assertEqual(res.status_code, 400, bad)

    def test_update_changes_only_supplied_fields_and_keeps_manual_config(self):
        self.put('shop-1', {'display_name': 'Old', 'defaults': {'branding': {'subtitle': 'seeded'}}})
        mapping = IntegrationTenantMapping.objects.get()
        project = mapping.project
        # a RastiChat admin customises things by hand
        project.subtitle = 'hand written'
        project.allowed_domains = 'manual.example.com'
        project.save()
        from teams.models import Team
        team = Team.objects.create(workspace=mapping.workspace, name='Manual team')

        res = self.put('shop-1', {'display_name': 'New', 'verified_domains': ['shop.example.com'],
                                  'defaults': {'branding': {'subtitle': 'attempted overwrite'}}})
        self.assertEqual(res.status_code, 200)
        project.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(project.name, 'New')
        self.assertEqual(mapping.workspace.name, 'New')
        self.assertEqual(project.subtitle, 'hand written')          # seed-only: never re-applied
        self.assertEqual(project.allowed_domains, 'manual.example.com, shop.example.com')
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())    # unrelated config untouched

    def test_removing_a_verified_domain_removes_only_synced_entries(self):
        self.put('shop-1', {'display_name': 'S', 'verified_domains': ['a.example.com', 'b.example.com']})
        project = IntegrationTenantMapping.objects.get().project
        project.allowed_domains += ', manual.example.com'
        project.save()
        self.put('shop-1', {'verified_domains': ['a.example.com']})
        project.refresh_from_db()
        self.assertEqual(project.allowed_domains, 'a.example.com, manual.example.com')
        # a manual entry the host later also "verifies" is never recorded as synced, so never removed
        self.put('shop-1', {'verified_domains': ['a.example.com', 'manual.example.com']})
        self.put('shop-1', {'verified_domains': ['a.example.com']})
        project.refresh_from_db()
        self.assertIn('manual.example.com', project.allowed_domains)

    def test_omitted_fields_are_not_touched(self):
        self.put('shop-1', {'display_name': 'S', 'verified_domains': ['a.example.com'], 'metadata': {'plan': 'pro'}})
        self.put('shop-1', {'display_name': 'S2'})
        mapping = IntegrationTenantMapping.objects.get()
        self.assertEqual(mapping.verified_domains, ['a.example.com'])
        self.assertEqual(mapping.metadata, {'plan': 'pro'})

    def test_concurrent_first_provision_race_resolves_to_one_tenant(self):
        """Simulate losing the unique-constraint race: the retry must take the update path, not duplicate."""
        from . import provisioning
        real = provisioning._create
        calls = {'n': 0}

        def racing_create(integration, key, ext, data):
            calls['n'] += 1
            if calls['n'] == 1:
                # another request commits the same tenant first
                real(integration, key, ext, data)
                from django.db import IntegrityError
                raise IntegrityError('duplicate key value violates unique constraint')
            return real(integration, key, ext, data)

        # run inside atomic savepoints exactly as ensure_tenant does
        with mock.patch.object(provisioning, '_create', side_effect=racing_create):
            res = self.put('shop-1', {'display_name': 'S'})
        self.assertIn(res.status_code, (200, 201))
        self.assertEqual(IntegrationTenantMapping.objects.count(), 1)

    def test_suspend_archive_restore_lifecycle(self):
        self.put('shop-1', {'display_name': 'S'})
        mapping = IntegrationTenantMapping.objects.get()
        visitor = Visitor.objects.create(project=mapping.project)
        session = VisitorSession.objects.create(visitor=visitor)

        res = self.put('shop-1', {'status': 'suspended'})
        self.assertEqual(res.json()['status'], 'suspended')
        mapping.refresh_from_db()
        self.assertFalse(mapping.workspace.is_active)
        self.assertFalse(mapping.project.is_active)
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)       # live visitor sessions die with the tenant

        res = self.put('shop-1', {'status': 'active'})
        mapping.refresh_from_db()
        self.assertTrue(mapping.workspace.is_active and mapping.project.is_active)

        res = self.host.call(self.client, 'delete', URL.format('shop-1'))
        self.assertEqual((res.status_code, res.json()['status']), (200, 'archived'))
        res = self.host.call(self.client, 'delete', URL.format('shop-1'))  # idempotent
        self.assertEqual(res.status_code, 200)
        res = self.put('shop-1', {'display_name': 'renamed'})
        self.assertEqual((res.status_code, self.error_code(res)), (409, 'tenant_archived'))
        res = self.put('shop-1', {'status': 'active'})
        self.assertEqual(res.status_code, 200)
        actions = list(AuditEvent.objects.filter(action='integration.tenant_status_changed').values_list('metadata', flat=True))
        self.assertEqual([a['to_status'] for a in actions], ['SUSPENDED', 'ACTIVE', 'ARCHIVED', 'ACTIVE'])

    def test_archive_retains_chat_history(self):
        self.put('shop-1', {'display_name': 'S'})
        mapping = IntegrationTenantMapping.objects.get()
        conv = Conversation.objects.create(workspace=mapping.workspace, type='CUSTOMER')
        self.host.call(self.client, 'delete', URL.format('shop-1'))
        self.assertTrue(Conversation.objects.filter(pk=conv.pk).exists())

    def test_provisioning_writes_no_sensitive_data_to_audit(self):
        self.put('shop-1', {'display_name': 'Secret Shop Name', 'metadata': {'plan': 'pro'}})
        for event in AuditEvent.objects.filter(action__startswith='integration.'):
            blob = json.dumps(event.metadata)
            self.assertNotIn('Secret Shop Name', blob)
            self.assertNotIn('Bearer', blob)


class CrossIntegrationIsolationTests(IntegrationTestBase):
    """A compromised integration must not reach another integration's tenants."""

    def setUp(self):
        super().setUp()
        self.other = FakeHost('other-host')
        self.put('shared-id', {'display_name': 'Acme tenant'})

    def test_same_external_id_in_two_integrations_are_distinct_tenants(self):
        res = self.put('shared-id', {'display_name': 'Other tenant'}, host=self.other)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(IntegrationTenantMapping.objects.count(), 2)
        self.assertEqual(len({m.workspace_id for m in IntegrationTenantMapping.objects.all()}), 2)

    def test_other_integration_cannot_read_update_or_archive(self):
        acme_mapping = IntegrationTenantMapping.objects.get(integration=self.host.integration)
        res = self.other.call(self.client, 'get', URL.format('shared-id'))
        self.assertEqual((res.status_code, self.error_code(res)), (404, 'tenant_not_found'))
        res = self.other.call(self.client, 'delete', URL.format('shared-id'))
        self.assertEqual(res.status_code, 404)
        acme_mapping.refresh_from_db()
        self.assertEqual(acme_mapping.status, IntegrationTenantMapping.Status.ACTIVE)
        # a PUT from the other integration creates ITS OWN tenant; acme's is unchanged
        self.put('shared-id', {'display_name': 'hijack'}, host=self.other)
        acme_mapping.refresh_from_db()
        self.assertEqual(acme_mapping.display_name, 'Acme tenant')

    def test_other_integrations_token_cannot_be_used_with_acme_issuer(self):
        res = self.host.call(self.client, 'get', URL.format('shared-id'), private=self.other.private)
        self.assertEqual(res.status_code, 401)


class ManagementCommandTests(TestCase):
    def test_register_integration_and_key(self):
        import os
        import tempfile
        from io import StringIO
        platform = Platform.objects.create(name='P', external_id='p-ext')
        call_command('integration_create', slug='my-app', name='My App', platform_external_id='p-ext')
        with tempfile.TemporaryDirectory() as tmp:
            call_command('integration_keygen', out_dir=tmp, name='k', stdout=StringIO())
            private_path = os.path.join(tmp, 'k.private.pem')
            self.assertEqual(oct(os.stat(private_path).st_mode & 0o777), '0o600')
            out = StringIO()
            call_command('integration_key_add', integration='my-app', public_key_file=os.path.join(tmp, 'k.public.pem'),
                         stdout=out)
        self.assertIn('kid=ick_', out.getvalue())
        integration = Integration.objects.get(slug='my-app')
        self.assertEqual(integration.platform, platform)
        self.assertEqual(integration.keys.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action__in=['integration.created', 'integration.key_added']).count(), 2)

    def test_private_key_is_refused(self):
        import os
        import tempfile
        from django.core.management.base import CommandError
        from .keys import generate_keypair
        Platform.objects.create(name='P', external_id='p-ext')
        call_command('integration_create', slug='my-app', name='My App', platform_external_id='p-ext')
        private_pem, _ = generate_keypair()
        with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as fh:
            fh.write(private_pem)
        try:
            with self.assertRaises(CommandError):
                call_command('integration_key_add', integration='my-app', public_key_file=fh.name)
        finally:
            os.unlink(fh.name)

    def test_disable_and_enable(self):
        Platform.objects.create(name='P', external_id='p-ext')
        call_command('integration_create', slug='my-app', name='My App', platform_external_id='p-ext')
        call_command('integration_set_active', slug='my-app', disable=True)
        self.assertFalse(Integration.objects.get(slug='my-app').is_active)
        call_command('integration_set_active', slug='my-app', enable=True)
        self.assertTrue(Integration.objects.get(slug='my-app').is_active)
