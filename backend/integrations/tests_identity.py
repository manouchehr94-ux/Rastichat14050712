import json

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from audit.models import AuditEvent
from conversations import ws_access
from conversations.models import Conversation, Message
from platforms.models import PlatformMembership
from teams.models import Team, TeamMembership
from visitors.models import Visitor, VisitorSession
from workspaces.models import WorkspaceMembership

from .models import ExternalIdentity, ExternalMembership, IntegrationTenantMapping
from .testing import FakeHost
from .tests import flush_redis_state

TENANT_URL = '/api/v1/integrations/tenants/{}/'
CUSTOMER_URL = '/api/v1/identity/customer/'
STAFF_URL = '/api/v1/identity/staff/'


class IdentityBase(TestCase):
    def setUp(self):
        flush_redis_state()
        self.client = APIClient()
        self.host = FakeHost('acme')
        self.t1 = self.provision(self.host, 'shop-1')
        self.t2 = self.provision(self.host, 'shop-2')

    def provision(self, host, tenant, **extra):
        res = host.call(self.client, 'put', TENANT_URL.format(tenant), {'display_name': f'Tenant {tenant}', **extra})
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def code(self, res):
        return res.json()['error']['code']

    def customer_login(self, host, tenant_info, sub, tenant, client=None, **kw):
        assertion = host.assertion('customer', sub, tenant, **kw)
        return (client or self.client).post(
            CUSTOMER_URL, {'project_key': tenant_info['project_public_key'], 'assertion': assertion}, format='json')

    def staff_login(self, host, sub, tenant=None, role='admin', actor='tenant_staff', **kw):
        return self.client.post(STAFF_URL, {'assertion': host.assertion(actor, sub, tenant, role, **kw)}, format='json')

    def auth(self, token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return client


class CustomerAssertionTests(IdentityBase):
    def test_trusted_customer_session_and_resume(self):
        res = self.customer_login(self.host, self.t1, 'cust-42', 'shop-1')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertTrue(data['identity']['verified'])
        visitor = Visitor.objects.get(pk=data['visitor_id'])
        self.assertEqual(visitor.external_id, 'int:acme:cust-42')
        self.assertEqual(str(visitor.project.public_key), self.t1['project_public_key'])

        start = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=data['session_token'])
        self.assertEqual(start.status_code, 200, start.content)
        conv_id = start.json()['id']

        # second login (new browser/session) resumes the same visitor and the same conversation
        again = self.customer_login(self.host, self.t1, 'cust-42', 'shop-1').json()
        self.assertEqual(again['visitor_id'], data['visitor_id'])
        self.assertNotEqual(again['session_token'], data['session_token'])
        start2 = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=again['session_token'])
        self.assertEqual(start2.json()['id'], conv_id)
        self.assertEqual(ExternalIdentity.objects.filter(kind='CUSTOMER').count(), 1)

    def test_same_external_user_in_two_tenants_are_two_isolated_visitors(self):
        a = self.customer_login(self.host, self.t1, 'cust-42', 'shop-1').json()
        b = self.customer_login(self.host, self.t2, 'cust-42', 'shop-2').json()
        self.assertNotEqual(a['visitor_id'], b['visitor_id'])
        conv_a = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=a['session_token']).json()['id']
        # B's session cannot read A's conversation
        res = self.client.get(f'/api/v1/widget/conversations/{conv_a}/messages/', HTTP_X_WIDGET_SESSION=b['session_token'])
        self.assertIn(res.status_code, (403, 404))

    def test_forged_assertions_are_refused(self):
        other = FakeHost('mallory')
        cases = {
            'foreign key, victim issuer': dict(private=other.private),
            'wrong audience': dict(audience='rastichat:api'),
            'wrong issuer': dict(issuer='mallory'),
            'unknown kid': dict(kid='ick_nope'),
            'expired': dict(iat=1_000_000_000),
        }
        for name, kw in cases.items():
            res = self.customer_login(self.host, self.t1, 'cust-42', 'shop-1', **kw)
            self.assertIn(res.status_code, (401, 403), name)
        self.assertFalse(Visitor.objects.filter(external_id__startswith='int:').exists())

    def test_replayed_assertion_is_refused(self):
        assertion = self.host.assertion('customer', 'cust-1', 'shop-1')
        body = {'project_key': self.t1['project_public_key'], 'assertion': assertion}
        self.assertEqual(self.client.post(CUSTOMER_URL, body, format='json').status_code, 200)
        res = self.client.post(CUSTOMER_URL, body, format='json')
        self.assertEqual((res.status_code, self.code(res)), (401, 'token_replayed'))

    def test_api_token_is_not_an_identity_assertion(self):
        token = self.host.token('POST', CUSTOMER_URL, b'', purpose='api', extra={'actor': 'customer', 'tenant': 'shop-1'})
        res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key'], 'assertion': token}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_assertion_for_one_tenant_is_useless_on_another_tenants_project(self):
        res = self.customer_login(self.host, self.t2, 'cust-1', 'shop-1')   # asserts shop-1, presents shop-2's project
        self.assertEqual((res.status_code, self.code(res)), (403, 'tenant_mismatch'))

    def test_another_integrations_assertion_is_useless_on_this_integrations_project(self):
        other = FakeHost('other-host')
        self.provision(other, 'shop-1')                      # same external tenant id, different integration
        res = self.customer_login(other, self.t1, 'cust-1', 'shop-1')
        self.assertEqual((res.status_code, self.code(res)), (403, 'tenant_mismatch'))

    def test_suspended_tenant_and_disabled_integration_and_revoked_key(self):
        self.host.call(self.client, 'put', TENANT_URL.format('shop-1'), {'status': 'suspended'})
        res = self.customer_login(self.host, self.t1, 'c', 'shop-1')
        self.assertEqual(res.status_code, 400)               # project inactive
        self.host.call(self.client, 'put', TENANT_URL.format('shop-1'), {'status': 'active'})
        self.host.integration.is_active = False
        self.host.integration.save()
        self.assertEqual(self.code(self.customer_login(self.host, self.t1, 'c', 'shop-1')), 'integration_disabled')
        self.host.integration.is_active = True
        self.host.integration.save()
        self.host.key.status = 'REVOKED'
        self.host.key.save()
        self.assertEqual(self.code(self.customer_login(self.host, self.t1, 'c', 'shop-1')), 'key_revoked')

    def test_missing_scope(self):
        host = FakeHost('staffonly', scopes=('tenants:write', 'identity:staff'))
        info = self.provision(host, 'x1')
        res = self.customer_login(host, info, 'c', 'x1')
        self.assertEqual((res.status_code, self.code(res)), (403, 'scope_denied'))

    def test_invalid_claims(self):
        for kw in ({'sub': ''}, {'tenant': 'bad id'}):
            assertion = self.host.assertion('customer', kw.get('sub', 'c'), kw.get('tenant', 'shop-1'))
            res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key'], 'assertion': assertion}, format='json')
            self.assertIn(res.status_code, (400, 401, 403))
        res = self.client.post(CUSTOMER_URL, {'project_key': 'not-a-uuid', 'assertion': 'x'}, format='json')
        self.assertEqual((res.status_code, self.code(res)), (400, 'invalid_project'))
        res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key']}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key'],
                                              'assertion': self.host.assertion('wizard', 'c', 'shop-1')}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_origin_policy_and_origin_claim(self):
        self.host.call(self.client, 'put', TENANT_URL.format('shop-1'), {'verified_domains': ['shop1.example.com']})
        body = lambda **kw: {'project_key': self.t1['project_public_key'], 'assertion': self.host.assertion('customer', 'c', 'shop-1', **kw)}  # noqa: E731
        res = self.client.post(CUSTOMER_URL, body(), format='json', HTTP_ORIGIN='https://evil.example.org')
        self.assertEqual((res.status_code, self.code(res)), (403, 'origin_not_allowed'))
        res = self.client.post(CUSTOMER_URL, body(), format='json')                       # no Origin on session creation
        self.assertEqual((res.status_code, self.code(res)), (403, 'origin_required'))
        res = self.client.post(CUSTOMER_URL, body(origin='https://shop1.example.com'), format='json',
                               HTTP_ORIGIN='https://shop1.example.com')
        self.assertEqual(res.status_code, 200)
        self.host.call(self.client, 'put', TENANT_URL.format('shop-1'), {'verified_domains': ['shop1.example.com', 'b.example.com']})
        res = self.client.post(CUSTOMER_URL, body(origin='https://shop1.example.com'), format='json',
                               HTTP_ORIGIN='https://b.example.com')
        self.assertEqual((res.status_code, self.code(res)), (403, 'origin_mismatch'))

    @override_settings(WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID=True)
    def test_legacy_unverified_external_id_cannot_claim_a_verified_customer(self):
        verified = self.customer_login(self.host, self.t1, 'cust-42', 'shop-1').json()
        res = self.client.post('/api/v1/widget/init/', {'project_key': self.t1['project_public_key'],
                                                        'external_id': 'int:acme:cust-42'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res.json()['visitor_id'], verified['visitor_id'])

    @override_settings(WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID=False)
    def test_browser_external_id_never_yields_a_verified_visitor(self):
        verified = self.customer_login(self.host, self.t1, 'cust-42', 'shop-1').json()
        res = self.client.post('/api/v1/widget/init/', {'project_key': self.t1['project_public_key'],
                                                        'external_id': 'int:acme:cust-42'}, format='json')
        self.assertNotEqual(res.json()['visitor_id'], verified['visitor_id'])
        self.assertFalse(hasattr(Visitor.objects.get(pk=res.json()['visitor_id']), 'external_identity'))

    def test_disabling_a_customer_revokes_sessions_and_blocks_login(self):
        data = self.customer_login(self.host, self.t1, 'cust-9', 'shop-1').json()
        res = self.host.call(self.client, 'post', TENANT_URL.format('shop-1') + 'customers/cust-9/disable/')
        self.assertEqual(res.status_code, 200)
        start = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=data['session_token'])
        self.assertEqual(start.status_code, 401)
        self.assertEqual(self.code(self.customer_login(self.host, self.t1, 'cust-9', 'shop-1')), 'identity_disabled')
        self.host.call(self.client, 'post', TENANT_URL.format('shop-1') + 'customers/cust-9/enable/')
        self.assertEqual(self.customer_login(self.host, self.t1, 'cust-9', 'shop-1').status_code, 200)


class GuestUpgradeTests(IdentityBase):
    def guest(self, info):
        res = self.client.post('/api/v1/widget/init/', {'project_key': info['project_public_key']}, format='json')
        return res.json()

    def test_guest_conversation_attaches_only_with_assertion_and_guest_session(self):
        guest = self.guest(self.t1)
        conv_id = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=guest['session_token']).json()['id']
        Message.objects.create(conversation_id=conv_id, sender_type='VISITOR', sender_visitor_id=guest['visitor_id'],
                               content='hi', client_message_id='m1')

        # assertion WITHOUT the guest credential: nothing moves
        res = self.customer_login(self.host, self.t1, 'cust-1', 'shop-1').json()
        self.assertEqual(res['identity']['conversations_attached'], 0)
        self.assertEqual(str(Conversation.objects.get(pk=conv_id).visitor_id), guest['visitor_id'])

        # assertion + the guest's own session: attached, guest session revoked
        assertion = self.host.assertion('customer', 'cust-1', 'shop-1')
        res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key'], 'assertion': assertion},
                               format='json', HTTP_X_WIDGET_SESSION=guest['session_token']).json()
        self.assertEqual(res['identity']['conversations_attached'], 1)
        self.assertEqual(str(Conversation.objects.get(pk=conv_id).visitor_id), res['visitor_id'])
        self.assertTrue(Message.objects.filter(conversation_id=conv_id, sender_visitor_id=res['visitor_id']).exists())
        old = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=guest['session_token'])
        self.assertEqual(old.status_code, 401)

    def test_guest_from_another_project_is_never_merged(self):
        guest_other = self.guest(self.t2)
        conv = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=guest_other['session_token']).json()['id']
        assertion = self.host.assertion('customer', 'cust-1', 'shop-1')
        res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key'], 'assertion': assertion},
                               format='json', HTTP_X_WIDGET_SESSION=guest_other['session_token']).json()
        self.assertEqual(res['identity']['conversations_attached'], 0)
        self.assertEqual(str(Conversation.objects.get(pk=conv).visitor_id), guest_other['visitor_id'])

    def test_existing_customer_history_is_not_exposed_to_the_guest_session(self):
        cust = self.customer_login(self.host, self.t1, 'cust-1', 'shop-1').json()
        cust_conv = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=cust['session_token']).json()['id']
        guest = self.guest(self.t1)
        res = self.client.get(f'/api/v1/widget/conversations/{cust_conv}/messages/', HTTP_X_WIDGET_SESSION=guest['session_token'])
        self.assertIn(res.status_code, (403, 404))

    def test_open_conversations_never_duplicate_for_the_verified_visitor(self):
        cust = self.customer_login(self.host, self.t1, 'cust-1', 'shop-1').json()
        keep = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=cust['session_token']).json()['id']
        guest = self.guest(self.t1)
        guest_conv = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=guest['session_token']).json()['id']
        assertion = self.host.assertion('customer', 'cust-1', 'shop-1')
        res = self.client.post(CUSTOMER_URL, {'project_key': self.t1['project_public_key'], 'assertion': assertion},
                               format='json', HTTP_X_WIDGET_SESSION=guest['session_token']).json()
        self.assertEqual(res['identity']['conversations_attached'], 0)
        self.assertEqual(str(Conversation.objects.get(pk=guest_conv).visitor_id), guest['visitor_id'])
        start = self.client.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=res['session_token'])
        self.assertEqual(start.json()['id'], keep)      # still exactly one open thread (no MultipleObjectsReturned)


class StaffAssertionTests(IdentityBase):
    def test_tenant_admin_sso_creates_dedicated_account_and_membership(self):
        res = self.staff_login(self.host, 'staff-7', 'shop-1', 'admin', name='Sara')
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        identity = ExternalIdentity.objects.get(kind='STAFF', external_user_id='staff-7')
        user = identity.user
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.email.endswith('@integration.invalid'))
        self.assertEqual(user.display_name, 'Sara')
        wm = WorkspaceMembership.objects.get(user=user)
        self.assertEqual((wm.role, wm.workspace_id), ('WORKSPACE_ADMIN', self.t1['workspace_id']))
        self.assertEqual(data['memberships'], [{'workspace_id': self.t1['workspace_id'], 'role': 'WORKSPACE_ADMIN'}])

        api = self.auth(data['access'])
        self.assertEqual(api.get('/api/v1/auth/me/').status_code, 200)
        self.assertEqual(api.get('/api/v1/conversations/customer/').status_code, 200)

    def test_sso_is_idempotent_and_updates_role(self):
        self.staff_login(self.host, 'staff-7', 'shop-1', 'admin')
        self.staff_login(self.host, 'staff-7', 'shop-1', 'admin')
        self.assertEqual(ExternalIdentity.objects.filter(kind='STAFF').count(), 1)
        self.assertEqual(WorkspaceMembership.objects.count(), 1)
        self.staff_login(self.host, 'staff-7', 'shop-1', 'operator')
        self.assertEqual(WorkspaceMembership.objects.get().role, 'WORKSPACE_OPERATOR')
        self.assertEqual(ExternalMembership.objects.get().external_role, 'operator')

    def test_roles_are_generic_only(self):
        res = self.staff_login(self.host, 'staff-7', 'shop-1', 'catalog_manager')
        self.assertEqual(res.status_code, 400)
        res = self.staff_login(self.host, 'staff-7', 'shop-1', None)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_multi_tenant_staff_has_exact_roles_per_tenant(self):
        self.staff_login(self.host, 'staff-7', 'shop-1', 'admin')
        data = self.staff_login(self.host, 'staff-7', 'shop-2', 'operator').json()
        roles = {m['workspace_id']: m['role'] for m in data['memberships']}
        self.assertEqual(roles, {self.t1['workspace_id']: 'WORKSPACE_ADMIN', self.t2['workspace_id']: 'WORKSPACE_OPERATOR'})
        api = self.auth(data['access'])
        # admin in shop-1 does NOT make them admin in shop-2: platform-support (admin-only) inbox must require the exact workspace
        res = api.post('/api/v1/support/', {'workspace_id': self.t2['workspace_id'], 'subject': 'x'}, format='json')
        self.assertEqual(res.status_code, 403)
        res = api.post('/api/v1/support/', {'workspace_id': self.t1['workspace_id'], 'subject': 'x'}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_staff_of_one_tenant_cannot_reach_another_tenants_conversations(self):
        conv = Conversation.objects.create(workspace_id=self.t2['workspace_id'], type='CUSTOMER')
        data = self.staff_login(self.host, 'staff-7', 'shop-1', 'admin').json()
        api = self.auth(data['access'])
        ids = [c['id'] for c in api.get('/api/v1/conversations/customer/').json()]
        self.assertNotIn(str(conv.id), ids)
        self.assertEqual(api.get(f'/api/v1/conversations/customer/{conv.id}/').status_code, 404)

    def test_assertion_cannot_target_another_integrations_tenant(self):
        other = FakeHost('other-host')
        self.provision(other, 'private-tenant')
        res = self.staff_login(self.host, 'staff-7', 'private-tenant', 'admin')
        self.assertEqual((res.status_code, self.code(res)), (403, 'tenant_unavailable'))
        self.assertFalse(WorkspaceMembership.objects.exists())

    def test_suspended_tenant_refuses_staff_sso(self):
        self.host.call(self.client, 'put', TENANT_URL.format('shop-1'), {'status': 'suspended'})
        res = self.staff_login(self.host, 'staff-7', 'shop-1', 'admin')
        self.assertEqual(self.code(res), 'tenant_unavailable')

    def test_customer_assertion_cannot_mint_a_staff_session(self):
        res = self.staff_login(self.host, 'c-1', 'shop-1', None, actor='customer')
        self.assertEqual(res.status_code, 400)

    def test_never_links_or_modifies_a_preexisting_user(self):
        from django.contrib.auth import get_user_model
        existing = get_user_model().objects.create_user(email='real-admin@example.com', password='pass1234')
        before = (existing.pk, existing.is_active, existing.email)
        self.staff_login(self.host, 'staff-7', 'shop-1', 'admin', name='Real Admin', email='real-admin@example.com')
        existing.refresh_from_db()
        self.assertEqual((existing.pk, existing.is_active, existing.email), before)
        self.assertFalse(existing.workspace_memberships.exists())
        self.assertNotEqual(ExternalIdentity.objects.get().user_id, existing.pk)

    def test_platform_staff_assertion(self):
        res = self.staff_login(self.host, 'plat-1', None, 'operator', actor='platform_staff')
        self.assertEqual(res.status_code, 200, res.content)
        pm = PlatformMembership.objects.get()
        self.assertEqual((pm.role, pm.platform_id), ('PLATFORM_SUPPORT_AGENT', self.host.platform.pk))
        self.assertEqual(res.json()['platform_roles'], ['PLATFORM_SUPPORT_AGENT'])
        api = self.auth(res.json()['access'])
        self.assertEqual(api.get('/api/v1/platform/support/').status_code, 200)

    def test_platform_staff_needs_platform_scope(self):
        host = FakeHost('nope', scopes=('tenants:write', 'identity:staff'))
        res = self.client.post(STAFF_URL, {'assertion': host.assertion('platform_staff', 'p', None, 'admin')}, format='json')
        self.assertEqual((res.status_code, self.code(res)), (403, 'scope_denied'))

    def test_session_lifetime_is_short(self):
        import jwt as pyjwt
        data = self.staff_login(self.host, 'staff-7', 'shop-1', 'admin').json()
        claims = pyjwt.decode(data['access'], options={'verify_signature': False})
        self.assertLessEqual(claims['exp'] - claims['iat'], 30 * 60 + 5)
        self.assertEqual(data['expires_in'], 30 * 60)

    def test_replay_and_forgery(self):
        a = self.host.assertion('tenant_staff', 's1', 'shop-1', 'admin')
        self.assertEqual(self.client.post(STAFF_URL, {'assertion': a}, format='json').status_code, 200)
        self.assertEqual(self.client.post(STAFF_URL, {'assertion': a}, format='json').status_code, 401)
        other = FakeHost('mallory')
        forged = self.host.assertion('tenant_staff', 's2', 'shop-1', 'owner', private=other.private)
        self.assertEqual(self.client.post(STAFF_URL, {'assertion': forged}, format='json').status_code, 401)
        self.assertFalse(ExternalIdentity.objects.filter(external_user_id='s2').exists())


class StaffDeprovisioningTests(IdentityBase):
    def setUp(self):
        super().setUp()
        data = self.staff_login(self.host, 'staff-7', 'shop-1', 'admin').json()
        self.token = data['access']
        self.api = self.auth(self.token)
        self.identity = ExternalIdentity.objects.get(external_user_id='staff-7')
        self.conv = Conversation.objects.create(workspace_id=self.t1['workspace_id'], type='CUSTOMER')

    def member_url(self, tenant='shop-1', user='staff-7'):
        return TENANT_URL.format(tenant) + f'members/{user}/'

    def test_membership_removal_is_prompt_and_complete(self):
        team = Team.objects.create(workspace_id=self.t1['workspace_id'], name='T')
        TeamMembership.objects.create(team=team, user=self.identity.user, role='SUPERVISOR')
        self.assertIsNotNone(ws_access.operator_conversation(self.identity.user, self.conv.id))
        res = self.host.call(self.client, 'delete', self.member_url())
        self.assertEqual((res.status_code, res.json()), (200, {'removed': True}))
        # REST: same still-valid JWT now sees nothing; WS access function (live revalidation) refuses
        self.assertIn(self.api.get(f'/api/v1/conversations/customer/{self.conv.id}/').status_code, (403, 404))
        self.assertEqual(self.api.get('/api/v1/conversations/customer/').status_code, 403)   # no membership left
        self.assertIsNone(ws_access.operator_conversation(self.identity.user, self.conv.id))
        self.assertFalse(TeamMembership.objects.filter(user=self.identity.user).exists())
        # idempotent
        self.assertEqual(self.host.call(self.client, 'delete', self.member_url()).json(), {'removed': False})
        self.assertTrue(Conversation.objects.filter(pk=self.conv.pk).exists())          # history kept
        self.assertTrue(AuditEvent.objects.filter(action='integration.membership_removed').exists())

    def test_removal_only_touches_this_integrations_own_memberships(self):
        from django.contrib.auth import get_user_model
        manual = get_user_model().objects.create_user(email='manual@x.com', password='pass1234')
        WorkspaceMembership.objects.create(user=manual, workspace_id=self.t1['workspace_id'], role='WORKSPACE_ADMIN')
        other = FakeHost('other-host')
        self.provision(other, 'shop-1')
        res = other.call(self.client, 'delete', self.member_url())           # other integration, same ids
        self.assertEqual(res.json(), {'removed': False})
        self.assertTrue(WorkspaceMembership.objects.filter(user=self.identity.user).exists())
        self.host.call(self.client, 'delete', self.member_url(user='manual'))  # not an external identity at all
        self.assertTrue(WorkspaceMembership.objects.filter(user=manual).exists())

    def test_put_member_provisions_ahead_of_login_and_updates_role(self):
        res = self.host.call(self.client, 'put', self.member_url(user='staff-8'), {'role': 'operator', 'display_name': 'Ali'})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(WorkspaceMembership.objects.get(user__external_identity__external_user_id='staff-8').role,
                         'WORKSPACE_OPERATOR')
        res = self.host.call(self.client, 'put', self.member_url(user='staff-8'), {'role': 'admin'})
        self.assertEqual(WorkspaceMembership.objects.get(user__external_identity__external_user_id='staff-8').role,
                         'WORKSPACE_ADMIN')
        self.assertEqual(self.host.call(self.client, 'put', self.member_url(user='staff-8'), {'role': 'god'}).status_code, 400)

    def test_disabling_a_user_cuts_off_everything_immediately(self):
        res = self.host.call(self.client, 'post', '/api/v1/integrations/users/staff-7/disable/')
        self.assertEqual((res.status_code, res.json()['changed']), (200, True))
        self.assertEqual(self.api.get('/api/v1/auth/me/').status_code, 401)           # still-unexpired JWT is dead
        self.assertIsNone(ws_access.operator_conversation(self.identity.user, self.conv.id))
        self.assertFalse(WorkspaceMembership.objects.filter(user=self.identity.user).exists())
        res = self.staff_login(self.host, 'staff-7', 'shop-1', 'admin')
        self.assertEqual((res.status_code, self.code(res)), (403, 'identity_disabled'))
        # re-enable does not resurrect access by itself: the host must assert again
        self.host.call(self.client, 'post', '/api/v1/integrations/users/staff-7/enable/')
        self.assertEqual(self.api.get('/api/v1/conversations/customer/').status_code, 403)   # active again but no membership
        self.assertEqual(self.staff_login(self.host, 'staff-7', 'shop-1', 'admin').status_code, 200)

    def test_suspending_the_tenant_cuts_staff_off(self):
        self.host.call(self.client, 'put', TENANT_URL.format('shop-1'), {'status': 'suspended'})
        self.assertIsNone(ws_access.operator_conversation(self.identity.user, self.conv.id))

    def test_scopes_guard_the_management_endpoints(self):
        host = FakeHost('narrow', scopes=('tenants:write', 'tenants:read'))
        self.provision(host, 'shop-1')
        res = host.call(self.client, 'put', self.member_url(user='x'), {'role': 'admin'})
        self.assertEqual((res.status_code, self.code(res)), (403, 'scope_denied'))
        res = host.call(self.client, 'post', '/api/v1/integrations/users/x/disable/')
        self.assertEqual(res.status_code, 403)

    def test_platform_member_removal(self):
        self.host.call(self.client, 'put', '/api/v1/integrations/platform/members/plat-1/', {'role': 'admin'})
        self.assertEqual(PlatformMembership.objects.get().role, 'PLATFORM_ADMIN')
        res = self.host.call(self.client, 'delete', '/api/v1/integrations/platform/members/plat-1/')
        self.assertEqual(res.json(), {'removed': True})
        self.assertFalse(PlatformMembership.objects.exists())
