"""Integration Contract §11: host-pushed, tenant-scoped customer context."""
from django.test import TestCase
from rest_framework.test import APIClient

from audit.models import AuditEvent

from .models import ExternalContext, ExternalIdentity
from .scopes import ALL_SCOPES, CONTEXT_WRITE, IDENTITY_CUSTOMER, TENANTS_WRITE
from .testing import FakeHost
from .tests import flush_redis_state

TENANT = '/api/v1/integrations/tenants/{}/'
CTX = TENANT + 'contexts/{}/'
CUSTOMER_URL = '/api/v1/identity/customer/'
STAFF_URL = '/api/v1/identity/staff/'


class ContextBase(TestCase):
    def setUp(self):
        flush_redis_state()
        self.client = APIClient()
        self.host = FakeHost('acme')
        self.t1 = self.host.call(self.client, 'put', TENANT.format('shop-1'), {'display_name': 'Shop 1'}).json()
        self.t2 = self.host.call(self.client, 'put', TENANT.format('shop-2'), {'display_name': 'Shop 2'}).json()

    def put(self, tenant, user, body, host=None):
        return (host or self.host).call(self.client, 'put', CTX.format(tenant, user), body)

    def code(self, res):
        return res.json()['error']['code']


class ContextApiTests(ContextBase):
    def test_push_get_replace_delete(self):
        res = self.put('shop-1', 'cust-1', {'profile': {'tier': 'gold', 'orders': 12}, 'context': {'page': '/cart', 'vip': True}})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()['profile'], {'tier': 'gold', 'orders': 12})
        got = self.host.call(self.client, 'get', CTX.format('shop-1', 'cust-1'))
        self.assertEqual((got.status_code, got.json()['context']), (200, {'page': '/cart', 'vip': True}))
        # PUT replaces the whole snapshot (idempotent): keys not sent are gone
        self.put('shop-1', 'cust-1', {'context': {'page': '/orders/7'}})
        got = self.host.call(self.client, 'get', CTX.format('shop-1', 'cust-1')).json()
        self.assertEqual((got['profile'], got['context']), ({}, {'page': '/orders/7'}))
        self.assertEqual(ExternalContext.objects.count(), 1)
        res = self.host.call(self.client, 'delete', CTX.format('shop-1', 'cust-1'))
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.host.call(self.client, 'get', CTX.format('shop-1', 'cust-1')).status_code, 404)
        # deleting again is not an error
        self.assertEqual(self.host.call(self.client, 'delete', CTX.format('shop-1', 'cust-1')).status_code, 204)

    def test_scope_is_required(self):
        host = FakeHost('narrow', scopes=[s for s in ALL_SCOPES if s != CONTEXT_WRITE])
        host.call(self.client, 'put', TENANT.format('n1'), {'display_name': 'N'})
        res = host.call(self.client, 'put', CTX.format('n1', 'u1'), {'context': {'a': 'b'}})
        self.assertEqual((res.status_code, self.code(res)), (403, 'scope_denied'))
        self.assertFalse(ExternalContext.objects.exists())

    def test_refused_payloads_store_nothing(self):
        cases = {
            'nested': ({'context': {'x': {'y': 1}}}, 'invalid_context'),
            'list value': ({'context': {'x': [1]}}, 'invalid_context'),
            'null value': ({'context': {'x': None}}, 'invalid_context'),
            'bad key': ({'context': {'Bad-Key': 'v'}}, 'invalid_context'),
            'credential key': ({'context': {'api_key': 'v'}}, 'sensitive_context_not_accepted'),
            'password key': ({'profile': {'user_password': 'v'}}, 'sensitive_context_not_accepted'),
            'jwt-like value': ({'context': {'note': 'eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ4In0.sig'}}, 'sensitive_context_not_accepted'),
            'sensitive section': ({'sensitive': {'a': 'b'}}, 'sensitive_context_not_accepted'),
            'unknown section': ({'extras': {'a': 'b'}}, 'invalid_context'),
            'too long value': ({'context': {'x': 'a' * 201}}, 'invalid_context'),
            'too many keys': ({'context': {f'k{i}': i for i in range(21)}}, 'invalid_context'),
            'too large overall': ({'profile': {f'p{i}': 'x' * 200 for i in range(20)}, 'context': {f'c{i}': 'y' * 200 for i in range(2)}},
                                   'context_too_large'),
        }
        for name, (body, code) in cases.items():
            res = self.put('shop-1', 'cust-1', body)
            self.assertEqual(res.status_code, 400, name)
            self.assertEqual(self.code(res), code, name)
        self.assertFalse(ExternalContext.objects.exists())
        self.assertFalse(ExternalIdentity.objects.exists())   # a refused push does not even create the identity

    def test_tenant_isolation(self):
        self.put('shop-1', 'cust-1', {'context': {'note': 'shop one'}})
        # same person id in another tenant is a different customer with no context
        self.assertEqual(self.host.call(self.client, 'get', CTX.format('shop-2', 'cust-1')).status_code, 404)
        self.put('shop-2', 'cust-1', {'context': {'note': 'shop two'}})
        self.assertEqual(ExternalContext.objects.count(), 2)
        # another integration can neither read nor write this integration's tenants (uniform answer: tenant unavailable)
        other = FakeHost('mallory')
        for method, body in (('get', None), ('put', {'context': {'a': 'b'}}), ('delete', None)):
            res = other.call(self.client, method, CTX.format('shop-1', 'cust-1'), body)
            self.assertEqual(res.status_code, 403, method)
        self.assertEqual(ExternalContext.objects.get(identity__tenant_mapping__external_tenant_id='shop-1').context, {'note': 'shop one'})

    def test_disabled_customer_cannot_receive_context(self):
        self.put('shop-1', 'cust-1', {'context': {'a': 'b'}})
        self.host.call(self.client, 'post', TENANT.format('shop-1') + 'customers/cust-1/disable/')
        res = self.put('shop-1', 'cust-1', {'context': {'a': 'c'}})
        self.assertEqual((res.status_code, self.code(res)), (403, 'identity_disabled'))

    def test_audit_records_key_names_never_values(self):
        self.put('shop-1', 'cust-1', {'profile': {'tier': 'SECRET-VALUE-123'}, 'context': {'page': '/x'}})
        event = AuditEvent.objects.filter(action='integration.context_updated').get()
        self.assertEqual((event.metadata['profile_keys'], event.metadata['context_keys']), (['tier'], ['page']))
        self.assertNotIn('SECRET-VALUE-123', str(event.metadata))

    def test_unknown_tenant_and_bad_ids(self):
        self.assertEqual(self.put('nope', 'cust-1', {'context': {'a': 'b'}}).status_code, 403)
        self.assertEqual(self.host.call(self.client, 'get', CTX.format('shop-1', 'ghost')).status_code, 404)


class ContextInAssertionAndOperatorViewTests(ContextBase):
    def login(self, sub, tenant='shop-1', info=None, **claims):
        assertion = self.host.assertion('customer', sub, tenant, **claims)
        return self.client.post(CUSTOMER_URL, {'project_key': (info or self.t1)['project_public_key'], 'assertion': assertion}, format='json')

    def operator(self, tenant='shop-1', sub='op-1'):
        res = self.client.post(STAFF_URL, {'assertion': self.host.assertion('tenant_staff', sub, tenant, 'operator')}, format='json')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.json()["access"]}')
        return client

    def start_conversation(self, session):
        res = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=session)
        return res.json()['id']

    def test_ctx_claim_is_stored_and_visible_to_the_tenants_operator_only(self):
        res = self.login('cust-1', ctx={'profile': {'tier': 'gold'}, 'context': {'page': '/pricing'}})
        self.assertEqual(res.status_code, 200, res.content)
        conv = self.start_conversation(res.json()['session_token'])
        panel = self.operator().get(f'/api/v1/conversations/customer/{conv}/customer-context/').json()
        self.assertEqual(panel['host_context']['profile'], {'tier': 'gold'})
        self.assertEqual(panel['host_context']['context'], {'page': '/pricing'})
        self.assertTrue(panel['identity_verified'])
        # an operator of ANOTHER tenant cannot open this conversation at all
        self.host.call(self.client, 'put', TENANT.format('shop-2') + 'members/op-2/', {'role': 'operator'})
        other = self.operator('shop-2', 'op-2')
        self.assertEqual(other.get(f'/api/v1/conversations/customer/{conv}/customer-context/').status_code, 404)

    def test_pushed_context_appears_for_the_same_verified_customer(self):
        self.put('shop-1', 'cust-9', {'context': {'order_ref': 'A-1001'}})
        res = self.login('cust-9')
        conv = self.start_conversation(res.json()['session_token'])
        panel = self.operator().get(f'/api/v1/conversations/customer/{conv}/customer-context/').json()
        self.assertEqual(panel['host_context']['context'], {'order_ref': 'A-1001'})

    def test_guest_has_no_host_context_even_with_the_same_external_id(self):
        # push context for "cust-2", then a GUEST (no assertion) claims nothing: the legacy path can never be that identity
        self.put('shop-1', 'cust-2', {'context': {'tier': 'gold'}})
        guest = self.client.post('/api/v1/widget/init/', {'project_key': self.t1['project_public_key'],
                                                          'external_id': 'int:acme:cust-2'}, format='json').json()
        conv = self.start_conversation(guest['session_token'])
        panel = self.operator().get(f'/api/v1/conversations/customer/{conv}/customer-context/').json()
        self.assertIsNone(panel['host_context'])
        self.assertFalse(panel['identity_verified'])

    def test_invalid_ctx_claim_fails_the_login_loudly(self):
        res = self.login('cust-3', ctx={'context': {'password': 'x'}})
        self.assertEqual((res.status_code, self.code(res)), (400, 'sensitive_context_not_accepted'))
        res = self.login('cust-3', ctx='not-an-object')
        self.assertEqual((res.status_code, self.code(res)), (400, 'invalid_context'))
