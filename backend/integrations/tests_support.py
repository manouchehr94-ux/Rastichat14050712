"""Integration Contract §12: a host's trusted backend opens a platform -> tenant support conversation."""
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import Conversation, Message
from notifications.models import Notification

from .testing import FakeHost

TENANT = '/api/v1/integrations/tenants/{}/'
URL = TENANT + 'support-conversations/'


class SupportInitiationTests(TestCase):
    def setUp(self):
        from .tests import flush_redis_state
        flush_redis_state()
        self.client = APIClient()
        self.host = FakeHost('acme')
        self.host.call(self.client, 'put', TENANT.format('t1'), {'display_name': 'Tenant One'})
        self.host.call(self.client, 'put', TENANT.format('t2'), {'display_name': 'Tenant Two'})
        self.host.call(self.client, 'put', TENANT.format('t1') + 'members/admin-1/', {'role': 'admin'})
        self.host.call(self.client, 'put', '/api/v1/integrations/platform/members/plat-1/', {'role': 'owner', 'display_name': 'Pat'})
        self.host.call(self.client, 'put', '/api/v1/integrations/platform/members/plat-agent/', {'role': 'operator'})

    def post(self, tenant='t1', key='k1', host=None, **body):
        payload = {'initiator_user_id': 'plat-1', 'subject': 'Invoice', 'message': 'Please review your invoice.', **body}
        return (host or self.host).call(self.client, 'post', URL.format(tenant), payload,
                                        extra_headers={'HTTP_IDEMPOTENCY_KEY': key} if key else None)

    def test_platform_owner_opens_a_thread_via_the_trusted_backend(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.post()
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body['created'], body['opened_by'], body['status']), (True, 'platform', 'WAITING_FOR_WORKSPACE'))
        conv = Conversation.objects.get(pk=body['conversation_id'])
        self.assertEqual(conv.workspace.integration_mapping.external_tenant_id, 't1')
        msg = conv.messages.get()
        self.assertEqual(msg.sender.external_identity.external_user_id, 'plat-1')               # attributable to the initiator
        # the tenant admin (SSO'd earlier) sees it in the normal support inbox and was notified
        from accounts.models import User
        admin = User.objects.get(external_identity__external_user_id='admin-1')
        api = APIClient()
        api.force_authenticate(admin)
        self.assertEqual([c['id'] for c in api.get('/api/v1/support/').json()], [str(conv.id)])
        self.assertTrue(Notification.objects.filter(recipient=admin, event_type='SUPPORT_MESSAGE').exists())

    def test_idempotency_key_replays_the_first_result(self):
        first = self.post(key='same')
        replay = self.post(key='same')
        self.assertEqual((first.status_code, replay.status_code), (201, 201))
        self.assertEqual(replay['Idempotent-Replayed'], 'true')
        self.assertEqual(first.json(), replay.json())
        self.assertEqual((Conversation.objects.count(), Message.objects.count()), (1, 1))

    def test_same_key_with_a_different_request_is_a_conflict(self):
        self.post(key='k')
        res = self.post(key='k', message='A different message')
        self.assertEqual((res.status_code, res.json()['error']['code']), (409, 'idempotency_conflict'))
        res = self.post(tenant='t2', key='k')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(Message.objects.count(), 1)

    def test_key_is_required(self):
        res = self.post(key=None)
        self.assertEqual((res.status_code, res.json()['error']['code']), (400, 'idempotency_key_required'))
        self.assertFalse(Conversation.objects.exists())

    def test_new_key_same_subject_resumes_the_one_active_thread(self):
        a = self.post(key='a').json()
        b = self.post(key='b', message='Following up').json()
        self.assertEqual((b['conversation_id'], b['created']), (a['conversation_id'], False))
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 2)

    def test_initiator_must_be_an_active_owner_or_admin_of_this_integrations_platform(self):
        for who in ('plat-agent', 'admin-1', 'nobody'):                       # operator-level platform staff, tenant staff, unknown
            res = self.post(key=f'k-{who}', initiator_user_id=who)
            self.assertEqual((res.status_code, res.json()['error']['code']), (403, 'initiator_not_authorized'), who)
        self.host.call(self.client, 'post', '/api/v1/integrations/users/plat-1/disable/')
        self.assertEqual(self.post(key='dis').status_code, 403)
        self.assertFalse(Conversation.objects.exists())

    def test_scope_is_required(self):
        narrow = FakeHost('narrow', scopes=('tenants:write', 'identity:platform', 'identity:staff'))
        narrow.call(self.client, 'put', TENANT.format('t1'), {'display_name': 'x'})
        res = self.post(host=narrow)
        self.assertEqual((res.status_code, res.json()['error']['code']), (403, 'scope_denied'))

    def test_another_integration_cannot_reach_this_tenant_or_borrow_its_platform_staff(self):
        other = FakeHost('other')
        other.call(self.client, 'put', TENANT.format('t1'), {'display_name': 'Other T1'})
        other.call(self.client, 'put', '/api/v1/integrations/platform/members/plat-1/', {'role': 'owner'})   # same ids, own platform
        res = self.post(host=other, key='x')
        self.assertEqual(res.status_code, 201)
        conv = Conversation.objects.get(pk=res.json()['conversation_id'])
        self.assertEqual(conv.workspace.integration_mapping.integration.slug, 'other')                  # its OWN tenant, never acme's
        self.assertFalse(Conversation.objects.filter(workspace__integration_mapping__integration=self.host.integration).exists())
        res = other.call(self.client, 'post', URL.format('only-acme'), {'initiator_user_id': 'plat-1', 'message': 'hi'},
                         extra_headers={'HTTP_IDEMPOTENCY_KEY': 'y'})
        self.assertIn(res.status_code, (403, 404))

    def test_unavailable_tenant_and_validation(self):
        self.host.call(self.client, 'put', TENANT.format('t2'), {'status': 'suspended'})
        self.assertEqual(self.post(tenant='t2', key='s').json()['error']['code'], 'tenant_unavailable')
        self.assertEqual(self.post(key='e', message='  ').json()['error']['code'], 'empty_message')
        self.assertEqual(self.post(tenant='missing', key='m').status_code, 403)
        self.assertFalse(Conversation.objects.exists())

    def test_purge_command(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone
        from .models import IdempotencyRecord
        self.post(key='old')
        IdempotencyRecord.objects.update(created_at=timezone.now() - timedelta(hours=72))
        self.post(key='new', message='Another thing', subject_key='other')
        call_command('integration_purge_idempotency', older_than_hours=48)
        self.assertEqual(list(IdempotencyRecord.objects.values_list('key', flat=True)), ['new'])
