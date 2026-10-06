"""Platform <-> tenant support threads (generic): platform-initiated conversations, resume/idempotency, close/reopen,
notifications, unread, isolation, realtime."""
from concurrent.futures import ThreadPoolExecutor

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from audit.models import AuditEvent
from notifications.models import Notification
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership

from .models import Conversation, Message
from .tests_ws_tickets import authenticate, open_ws

User = get_user_model()
START = '/api/v1/platform/support/start/'


class CommitAwareClient(APIClient):
    """Runs `transaction.on_commit` hooks (notifications, realtime broadcast) inside Django's TestCase transaction, the way
    a real commit would."""

    def __init__(self, case, *a, **kw):
        super().__init__(*a, **kw)
        self._case = case

    def request(self, **kw):
        capture = getattr(self._case, 'captureOnCommitCallbacks', None)
        if capture is None:                     # TransactionTestCase: hooks run on real commits
            return super().request(**kw)
        with capture(execute=True):
            return super().request(**kw)


class World:
    def build(self):
        self.platform = Platform.objects.create(name='P1')
        self.ws_a = Workspace.objects.create(name='Tenant A', platform=self.platform)
        self.ws_b = Workspace.objects.create(name='Tenant B', platform=self.platform)
        self.other_platform = Platform.objects.create(name='P2')
        self.ws_x = Workspace.objects.create(name='Foreign', platform=self.other_platform)
        self.owner = self.user('owner', platform=(self.platform, 'PLATFORM_OWNER'))
        self.pl_admin = self.user('pladmin', platform=(self.platform, 'PLATFORM_ADMIN'))
        self.agent = self.user('agent', platform=(self.platform, 'PLATFORM_SUPPORT_AGENT'))
        self.foreign_owner = self.user('fowner', platform=(self.other_platform, 'PLATFORM_OWNER'))
        self.admin_a = self.user('admin-a', ws=(self.ws_a, 'WORKSPACE_ADMIN'))
        self.owner_a = self.user('owner-a', ws=(self.ws_a, 'WORKSPACE_OWNER'))
        self.op_a = self.user('op-a', ws=(self.ws_a, 'WORKSPACE_OPERATOR'))
        self.admin_b = self.user('admin-b', ws=(self.ws_b, 'WORKSPACE_ADMIN'))
        # admin of A who is merely an operator of B (the classic dangerous combination)
        self.mixed = self.user('mixed', ws=(self.ws_a, 'WORKSPACE_ADMIN'))
        WorkspaceMembership.objects.create(user=self.mixed, workspace=self.ws_b, role='WORKSPACE_OPERATOR')

    def user(self, name, platform=None, ws=None):
        u = User.objects.create_user(email=f'{name}@t.com', password='pass1234')
        if platform:
            PlatformMembership.objects.create(user=u, platform=platform[0], role=platform[1])
        if ws:
            WorkspaceMembership.objects.create(user=u, workspace=ws[0], role=ws[1])
        return u

    def api(self, user):
        c = CommitAwareClient(self)
        c.force_authenticate(user)
        return c

    def start(self, user, ws, **extra):
        body = {'workspace_id': ws.pk, 'subject': 'Billing notice', 'message': 'Hello from the platform',
                'client_message_id': 'c1', **extra}
        return self.api(user).post(START, body, format='json')


class PlatformInitiatedTests(World, TestCase):
    def setUp(self):
        self.build()

    def test_platform_owner_opens_a_thread_without_the_tenant_writing_first(self):
        res = self.start(self.owner, self.ws_a)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertTrue(body['created'])
        self.assertEqual((body['opened_by_side'], body['status'], body['workspace_name']),
                         ('PLATFORM', 'WAITING_FOR_WORKSPACE', 'Tenant A'))
        conv = Conversation.objects.get(pk=body['id'])
        self.assertEqual(conv.type, 'PLATFORM_SUPPORT')
        msg = conv.messages.get()
        self.assertEqual((msg.content, msg.sender_id, msg.sender_type), ('Hello from the platform', self.owner.pk, 'USER'))
        self.assertTrue(AuditEvent.objects.filter(action='support_conversation_created', target_id=str(conv.id),
                                                  metadata__opened_by='platform').exists())

    def test_platform_admin_may_open_but_support_agent_and_foreign_platform_may_not(self):
        self.assertEqual(self.start(self.pl_admin, self.ws_a, client_message_id='c2').status_code, 201)
        self.assertEqual(self.start(self.agent, self.ws_b).status_code, 404)              # agents reply, they do not initiate
        res = self.start(self.foreign_owner, self.ws_a)                                    # another platform's owner
        self.assertEqual((res.status_code, res.json()['code']), (404, 'workspace_not_found'))
        res = self.start(self.owner, self.ws_x)                                            # a workspace of another platform
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.start(self.admin_a, self.ws_a).status_code, 403)             # tenant admins use /support/start/
        self.assertEqual(APIClient().post(START, {}, format='json').status_code, 401)
        self.assertEqual(Conversation.objects.filter(workspace=self.ws_x).count(), 0)

    def test_start_validation(self):
        for body in ({'workspace_id': 'x', 'message': 'hi'}, {'message': 'hi'}):
            self.assertEqual(self.api(self.owner).post(START, body, format='json').status_code, 404)
        res = self.api(self.owner).post(START, {'workspace_id': self.ws_a.pk, 'message': '   '}, format='json')
        self.assertEqual((res.status_code, res.json()['code']), (400, 'empty_message'))
        res = self.api(self.owner).post(START, {'workspace_id': self.ws_a.pk, 'message': 'x' * 5001}, format='json')
        self.assertEqual(res.json()['code'], 'message_too_long')
        self.assertFalse(Conversation.objects.exists())

    def test_inactive_tenant_is_refused(self):
        self.ws_a.is_active = False
        self.ws_a.save()
        res = self.start(self.owner, self.ws_a)
        self.assertEqual((res.status_code, res.json()['code']), (409, 'tenant_unavailable'))
        self.assertFalse(Conversation.objects.exists())

    def test_start_is_idempotent_per_subject_and_resumes_the_active_thread(self):
        first = self.start(self.owner, self.ws_a).json()
        again = self.start(self.owner, self.ws_a, client_message_id='c1')                 # exact retry
        self.assertEqual((again.status_code, again.json()['id'], again.json()['created']), (200, first['id'], False))
        self.assertEqual(Message.objects.count(), 1)                                       # retry did not duplicate the message
        follow = self.start(self.pl_admin, self.ws_a, client_message_id='c2', message='Second note')
        self.assertEqual((follow.json()['id'], follow.json()['created']), (first['id'], False))
        self.assertEqual(Message.objects.filter(conversation_id=first['id']).count(), 2)
        other_subject = self.start(self.owner, self.ws_a, client_message_id='c3', subject_key='contract-renewal').json()
        self.assertNotEqual(other_subject['id'], first['id'])
        other_tenant = self.start(self.owner, self.ws_b, client_message_id='c4').json()
        self.assertNotEqual(other_tenant['id'], first['id'])

    def test_a_closed_thread_is_not_reused_by_start(self):
        first = self.start(self.owner, self.ws_a).json()
        self.assertEqual(self.api(self.owner).post(f'/api/v1/platform/support/{first["id"]}/close/').status_code, 200)
        second = self.start(self.owner, self.ws_a, client_message_id='c9').json()
        self.assertTrue(second['created'])
        self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(Conversation.objects.filter(workspace=self.ws_a, status__in=Conversation.ACTIVE_STATUSES).count(), 1)

    def test_database_guarantees_one_active_thread_per_subject(self):
        from django.db import IntegrityError, transaction
        Conversation.objects.create(workspace=self.ws_a, type='PLATFORM_SUPPORT', subject_key='k', status='OPEN')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Conversation.objects.create(workspace=self.ws_a, type='PLATFORM_SUPPORT', subject_key='k', status='WAITING_FOR_PLATFORM')
        Conversation.objects.create(workspace=self.ws_a, type='PLATFORM_SUPPORT', subject_key='k', status='CLOSED')   # history may repeat
        Conversation.objects.create(workspace=self.ws_a, type='PLATFORM_SUPPORT', subject_key='', status='OPEN')       # legacy rows exempt
        Conversation.objects.create(workspace=self.ws_a, type='PLATFORM_SUPPORT', subject_key='', status='OPEN')

    def test_tenant_admins_see_it_everyone_else_does_not(self):
        conv = self.start(self.owner, self.ws_a).json()['id']
        for user in (self.admin_a, self.owner_a):
            ids = [c['id'] for c in self.api(user).get('/api/v1/support/').json()]
            self.assertEqual(ids, [conv], user.email)
            self.assertEqual(self.api(user).get(f'/api/v1/support/{conv}/messages/').status_code, 200)
        for user in (self.op_a, self.admin_b, self.mixed):                                  # operator; other tenant; admin-of-A-only-operator-of-B
            api = self.api(user)
            if user == self.mixed:                                                          # admin of A: legitimately sees A's thread
                self.assertEqual([c['id'] for c in api.get('/api/v1/support/').json()], [conv])
                continue
            self.assertIn(api.get('/api/v1/support/').status_code, (200, 403))
            listed = api.get('/api/v1/support/')
            if listed.status_code == 200:
                self.assertEqual(listed.json(), [])
            self.assertIn(api.get(f'/api/v1/support/{conv}/messages/').status_code, (403, 404))
        # B's data never leaks to A and vice versa
        conv_b = self.start(self.owner, self.ws_b, client_message_id='cb').json()['id']
        self.assertEqual([c['id'] for c in self.api(self.admin_b).get('/api/v1/support/').json()], [conv_b])
        self.assertEqual([c['id'] for c in self.api(self.admin_a).get('/api/v1/support/').json()], [conv])
        self.assertEqual(self.api(self.admin_a).post(f'/api/v1/support/{conv_b}/send_message/',
                                                      {'content': 'x', 'client_message_id': 'z'}, format='json').status_code, 404)
        self.assertEqual(self.api(self.mixed).post(f'/api/v1/support/{conv_b}/close/').status_code, 404)

    def test_tenant_admins_are_notified_platform_and_operators_are_not(self):
        conv = self.start(self.owner, self.ws_a).json()['id']
        notified = set(Notification.objects.filter(event_type='SUPPORT_MESSAGE').values_list('recipient__email', flat=True))
        self.assertEqual(notified, {'admin-a@t.com', 'owner-a@t.com', 'mixed@t.com'})     # admins/owners of THAT tenant only
        n = Notification.objects.filter(recipient=self.admin_a).get()
        self.assertEqual(n.payload['conversation_id'], conv)
        self.assertIn('پشتیبانی پلتفرم', n.title)

    def test_full_round_trip_unread_read_and_status(self):
        conv = self.start(self.owner, self.ws_a).json()['id']
        admin = self.api(self.admin_a)
        row = admin.get('/api/v1/support/').json()[0]
        self.assertEqual((row['unread_count'], row['status'], row['opened_by_side']), (1, 'WAITING_FOR_WORKSPACE', 'PLATFORM'))
        self.assertEqual(admin.post(f'/api/v1/support/{conv}/mark_read/').status_code, 200)
        self.assertEqual(admin.get('/api/v1/support/').json()[0]['unread_count'], 0)
        res = admin.post(f'/api/v1/support/{conv}/send_message/', {'content': 'Thanks, noted', 'client_message_id': 'a1'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(admin.get('/api/v1/support/').json()[0]['unread_count'], 0)        # own message is not unread
        platform = self.api(self.owner)
        prow = platform.get('/api/v1/platform/support/').json()[0]
        self.assertEqual((prow['status'], prow['unread_count'], prow['workspace_name']), ('WAITING_FOR_PLATFORM', 1, 'Tenant A'))
        self.assertTrue(Notification.objects.filter(recipient=self.owner, event_type='SUPPORT_MESSAGE').exists())
        self.assertTrue(Notification.objects.filter(recipient=self.agent, event_type='SUPPORT_MESSAGE').exists())
        self.assertIn('Tenant A', Notification.objects.filter(recipient=self.owner, event_type='SUPPORT_MESSAGE').first().title)
        self.assertFalse(Notification.objects.filter(recipient=self.admin_a, payload__from='tenant').exists())   # never notifies the sender
        # duplicate client_message_id is a 409, empty/too long are 400 with codes
        dup = admin.post(f'/api/v1/support/{conv}/send_message/', {'content': 'again', 'client_message_id': 'a1'}, format='json')
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(admin.post(f'/api/v1/support/{conv}/send_message/', {'content': '', 'client_message_id': 'a2'}, format='json').status_code, 400)
        reply = platform.post(f'/api/v1/platform/support/{conv}/reply/', {'content': 'You are welcome', 'client_message_id': 'p2'}, format='json')
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(admin.get('/api/v1/support/').json()[0]['status'], 'WAITING_FOR_WORKSPACE')

    def test_close_and_reopen_from_both_sides_with_permissions(self):
        conv = self.start(self.owner, self.ws_a).json()['id']
        closed = self.api(self.admin_a).post(f'/api/v1/support/{conv}/close/')
        self.assertEqual((closed.status_code, closed.json()['status']), (200, 'CLOSED'))
        self.assertEqual(self.api(self.admin_a).post(f'/api/v1/support/{conv}/close/').json()['status'], 'CLOSED')   # idempotent
        reopened = self.api(self.owner).post(f'/api/v1/platform/support/{conv}/reopen/')
        self.assertEqual(reopened.json()['status'], 'WAITING_FOR_WORKSPACE')
        for user in (self.op_a, self.admin_b):
            self.assertIn(self.api(user).post(f'/api/v1/support/{conv}/close/').status_code, (403, 404))
        self.assertEqual(self.api(self.foreign_owner).post(f'/api/v1/platform/support/{conv}/close/').status_code, 404)
        self.assertEqual(Conversation.objects.get(pk=conv).status, 'WAITING_FOR_WORKSPACE')
        self.assertEqual(AuditEvent.objects.filter(target_id=conv, action__in=[
            'support_conversation_closed', 'support_conversation_reopened']).count(), 2)

    def test_a_reply_reopens_a_closed_thread_and_history_is_kept(self):
        conv = self.start(self.owner, self.ws_a).json()['id']
        self.api(self.owner).post(f'/api/v1/platform/support/{conv}/close/')
        res = self.api(self.admin_a).post(f'/api/v1/support/{conv}/send_message/', {'content': 'one more thing', 'client_message_id': 'late'}, format='json')
        self.assertEqual(res.status_code, 201)
        c = Conversation.objects.get(pk=conv)
        self.assertEqual((c.status, c.closed_at), ('WAITING_FOR_PLATFORM', None))
        self.assertEqual(c.messages.count(), 2)

    def test_reopen_refuses_when_another_thread_on_the_subject_is_active(self):
        first = self.start(self.owner, self.ws_a).json()['id']
        self.api(self.owner).post(f'/api/v1/platform/support/{first}/close/')
        self.start(self.owner, self.ws_a, client_message_id='n1')
        res = self.api(self.owner).post(f'/api/v1/platform/support/{first}/reopen/')
        self.assertEqual((res.status_code, res.json()['code']), (409, 'thread_already_active'))

    def test_platform_workspace_picker_lists_only_own_platform_active_tenants(self):
        self.ws_b.is_active = False
        self.ws_b.save()
        listed = self.api(self.owner).get('/api/v1/platform/support/workspaces/').json()
        self.assertEqual([w['name'] for w in listed], ['Tenant A'])
        self.assertEqual(self.api(self.agent).get('/api/v1/platform/support/workspaces/').json(), [])     # agents cannot initiate
        self.assertEqual([w['name'] for w in self.api(self.foreign_owner).get('/api/v1/platform/support/workspaces/').json()], ['Foreign'])
        self.assertEqual(self.api(self.owner).get('/api/v1/platform/support/workspaces/', {'q': 'zzz'}).json(), [])


class TenantStartTests(World, TestCase):
    def setUp(self):
        self.build()

    def test_tenant_admin_start_creates_then_resumes(self):
        api = self.api(self.admin_a)
        first = api.post('/api/v1/support/start/', {'subject': 'Help', 'message': 'Need help', 'client_message_id': 't1'}, format='json')
        self.assertEqual((first.status_code, first.json()['created'], first.json()['opened_by_side']), (201, True, 'TENANT'))
        again = api.post('/api/v1/support/start/', {'message': 'More detail', 'client_message_id': 't2'}, format='json')
        self.assertEqual((again.status_code, again.json()['id'], again.json()['created']), (200, first.json()['id'], False))
        retry = api.post('/api/v1/support/start/', {'message': 'More detail', 'client_message_id': 't2'}, format='json')
        self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(retry.json()['id'], first.json()['id'])
        self.assertTrue(Notification.objects.filter(recipient=self.owner, event_type='SUPPORT_MESSAGE').exists())

    def test_multi_workspace_admin_must_pick_exactly_one_they_administer(self):
        WorkspaceMembership.objects.create(user=self.admin_a, workspace=self.ws_b, role='WORKSPACE_ADMIN')
        api = self.api(self.admin_a)
        res = api.post('/api/v1/support/start/', {'message': 'x', 'client_message_id': 'm1'}, format='json')
        self.assertEqual(res.status_code, 400)                                                 # never "pick the first membership"
        res = api.post('/api/v1/support/start/', {'workspace_id': self.ws_b.pk, 'message': 'x', 'client_message_id': 'm2'}, format='json')
        self.assertEqual(Conversation.objects.get(pk=res.json()['id']).workspace_id, self.ws_b.pk)
        self.assertEqual(self.api(self.mixed).post('/api/v1/support/start/', {'workspace_id': self.ws_b.pk, 'message': 'x',
                                                                             'client_message_id': 'm3'}, format='json').status_code, 403)

    def test_operator_cannot_start(self):
        res = self.api(self.op_a).post('/api/v1/support/start/', {'message': 'x', 'client_message_id': 'o'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Conversation.objects.exists())

    def test_both_directions_share_one_thread(self):
        first = self.api(self.admin_a).post('/api/v1/support/start/', {'message': 'Question', 'client_message_id': 'q'}, format='json').json()
        platform = self.start(self.owner, self.ws_a, client_message_id='p', message='Answer')
        self.assertEqual((platform.json()['id'], platform.json()['created']), (first['id'], False))
        self.assertEqual(Conversation.objects.count(), 1)


@override_settings(WS_REVALIDATE_SECONDS=0)
class RealtimeTests(World, TransactionTestCase):
    def setUp(self):
        self.build()

    async def test_platform_message_reaches_the_tenants_open_support_socket_and_reply_returns(self):
        conv = await database_sync_to_async(lambda: self.start(self.owner, self.ws_a).json()['id'])()

        def ticket(user):
            res = self.api(user).post('/api/v1/ws/ticket/', {'kind': 'support', 'conversation_id': conv}, format='json')
            assert res.status_code == 201, res.content
            return res.json()['ticket']
        tenant = await open_ws(f'/ws/v2/support/{conv}/')
        await authenticate(tenant, await database_sync_to_async(ticket)(self.admin_a))
        self.assertEqual((await tenant.receive_json_from(timeout=3))['type'], 'auth.ok')
        platform = await open_ws(f'/ws/v2/support/{conv}/')
        await authenticate(platform, await database_sync_to_async(ticket)(self.owner))
        self.assertEqual((await platform.receive_json_from(timeout=3))['type'], 'auth.ok')

        await database_sync_to_async(lambda: self.start(self.owner, self.ws_a, client_message_id='live', message='Live from platform'))()
        got = await tenant.receive_json_from(timeout=3)
        self.assertEqual(got['content'], 'Live from platform')
        await platform.receive_json_from(timeout=3)                                          # platform's own echo

        await tenant.send_json_to({'message': 'Tenant replying', 'client_message_id': 'tw1'})
        self.assertEqual((await platform.receive_json_from(timeout=3))['content'], 'Tenant replying')
        status = await database_sync_to_async(lambda: Conversation.objects.get(pk=conv).status)()
        self.assertEqual(status, 'WAITING_FOR_PLATFORM')                                     # the socket path goes through the same service
        notified = await database_sync_to_async(lambda: Notification.objects.filter(recipient=self.owner, payload__from='tenant').count())()
        self.assertEqual(notified, 1)

        # an admin of another tenant cannot even obtain a ticket for this thread
        denied = await database_sync_to_async(lambda: self.api(self.admin_b).post(
            '/api/v1/ws/ticket/', {'kind': 'support', 'conversation_id': conv}, format='json'))()
        self.assertEqual(denied.status_code, 404)
        await tenant.disconnect()
        await platform.disconnect()

    async def test_concurrent_starts_converge_on_one_thread(self):
        def one(i):
            try:
                return self.start(self.owner, self.ws_a, client_message_id=f'cc{i}', message=f'm{i}').json().get('id')
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = await database_sync_to_async(lambda: list(pool.map(one, range(6))), thread_sensitive=False)()
        self.assertEqual(len({i for i in ids if i}), 1)
        count = await database_sync_to_async(lambda: Conversation.objects.filter(workspace=self.ws_a, type='PLATFORM_SUPPORT').count())()
        self.assertEqual(count, 1)
        msgs = await database_sync_to_async(lambda: Message.objects.count())()
        self.assertEqual(msgs, 6)
