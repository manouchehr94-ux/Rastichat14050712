"""Generic integration E2E — an unrelated host application (NOT RastiSi) uses RastiChat end to end, through the public
contract only:

    host provisions tenant -> widget config -> identity bootstrap (customer + staff SSO) -> optional pre-chat ->
    customer message over a real WebSocket -> operator inbox -> operator reply -> customer receives it in realtime.

No RastiChat code is modified or special-cased for this host; `FakeHost` knows nothing but the Integration Contract v1.
"""
from channels.db import database_sync_to_async
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from conversations.models import Conversation, Message
from conversations.tests_ws_tickets import authenticate, open_ws

from .testing import FakeHost

TENANT = '/api/v1/integrations/tenants/{}/'

STRUCTURED = {'pre_chat': {'enabled': True, 'title': 'Before we start', 'fields': [
    {'key': 'topic', 'type': 'select', 'label': 'Topic', 'required': True, 'order': 1,
     'choices': [{'value': 'billing', 'label': 'Billing'}, {'value': 'course', 'label': 'Course'}]},
    {'key': 'agree', 'type': 'consent', 'label': 'I accept', 'required': True, 'order': 2}]}}
ONE_QUESTION = {'pre_chat': {'enabled': True, 'fields': [
    {'key': 'help', 'type': 'text', 'label': 'What can we help you with?', 'required': True}]}}


@override_settings(WS_REVALIDATE_SECONDS=0)
class GenericHostEndToEnd(TransactionTestCase):
    def setUp(self):
        from .tests import flush_redis_state
        flush_redis_state()
        self.host = FakeHost('acme-learn')

    # ------------------------------------------------------------------ host-side helpers (what a host's backend does)
    def provision(self, tenant, name, widget):
        res = self.host.call(APIClient(), 'put', TENANT.format(tenant),
                             {'display_name': name, 'verified_domains': [f'{tenant}.acme.example'], 'defaults': {'widget': widget}})
        assert res.status_code == 201, res.content
        return res.json()

    def staff_session(self, tenant, user, role):
        host_client = APIClient()
        assert self.host.call(host_client, 'put', TENANT.format(tenant) + f'members/{user}/', {'role': role}).status_code == 200
        res = APIClient().post('/api/v1/identity/staff/', {'assertion': self.host.assertion('tenant_staff', user, tenant, role)}, format='json')
        assert res.status_code == 200, res.content
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {res.json()["access"]}')
        return api

    def customer_session(self, info, tenant, user, origin):
        res = APIClient().post('/api/v1/identity/customer/', {
            'project_key': info['project_public_key'], 'assertion': self.host.assertion('customer', user, tenant, origin=origin)},
            format='json', HTTP_ORIGIN=origin)
        assert res.status_code == 200, res.content
        return res.json()['session_token']

    def guest_session(self, info, origin):
        res = APIClient().post('/api/v1/widget/init/', {'project_key': info['project_public_key']}, format='json', HTTP_ORIGIN=origin)
        assert res.status_code == 200, res.content
        return res.json()['session_token']

    async def widget_socket(self, token, conv_id):
        res = await database_sync_to_async(lambda: APIClient().post(
            '/api/v1/widget/ws-ticket/', {'conversation_id': conv_id}, format='json', HTTP_X_WIDGET_SESSION=token))()
        self.assertEqual(res.status_code, 201, res.content)
        comm = await open_ws(f'/ws/v2/widget/{conv_id}/')
        await authenticate(comm, res.json()['ticket'])
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        return comm

    async def operator_socket(self, api, conv_id):
        res = await database_sync_to_async(lambda: api.post(
            '/api/v1/ws/ticket/', {'kind': 'dashboard_chat', 'conversation_id': conv_id}, format='json'))()
        self.assertEqual(res.status_code, 201, res.content)
        comm = await open_ws(f'/ws/v2/dashboard/{conv_id}/')
        await authenticate(comm, res.json()['ticket'])
        self.assertEqual((await comm.receive_json_from(timeout=3))['type'], 'auth.ok')
        return comm

    # ------------------------------------------------------------------------------------------ the scenarios
    async def test_mode_a_icon_only_no_questions_authenticated_customer_full_round_trip(self):
        origin = 'https://org-a.acme.example'
        info = await database_sync_to_async(self.provision)('org-a', 'Acme Academy', {'launcher': {'mode': 'icon'}})

        # widget configuration: a small icon-only launcher, no questions, conversation created on the first message
        config = await database_sync_to_async(lambda: APIClient().get(
            '/api/v1/widget/config/', {'project_key': info['project_public_key']}, HTTP_ORIGIN=origin).json())()
        self.assertEqual(config['launcher']['mode'], 'icon')
        self.assertFalse(config['pre_chat']['enabled'])
        self.assertEqual(config['behavior']['start_mode'], 'on_first_message')
        self.assertEqual(config['project']['name'], 'Acme Academy')

        # identity: the host backend asserts the customer; no second login, no browser-supplied identity
        token = await database_sync_to_async(self.customer_session)(info, 'org-a', 'alice', origin)
        start_api = APIClient()

        def start():
            return start_api.post('/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=token)
        conv = (await database_sync_to_async(start)()).json()
        customer = await self.widget_socket(token, conv['id'])

        # the receiver side is the ordinary RastiChat engine
        operator_api = await database_sync_to_async(self.staff_session)('org-a', 'bob', 'operator')
        operator = await self.operator_socket(operator_api, conv['id'])

        await customer.send_json_to({'message': 'I have a question about my order.', 'client_message_id': 'c1'})
        echo = await customer.receive_json_from(timeout=3)
        self.assertEqual(echo['content'], 'I have a question about my order.')
        seen_by_operator = await operator.receive_json_from(timeout=3)
        self.assertEqual(seen_by_operator['content'], 'I have a question about my order.')

        inbox = await database_sync_to_async(lambda: operator_api.get('/api/v1/conversations/customer/').json())()
        self.assertEqual([c['id'] for c in inbox], [conv['id']])
        context = await database_sync_to_async(lambda: operator_api.get(
            f'/api/v1/conversations/customer/{conv["id"]}/customer-context/').json())()
        self.assertTrue(context['identity_verified'])

        await operator.send_json_to({'message': 'Happy to help!', 'client_message_id': 'o1'})
        reply = await customer.receive_json_from(timeout=3)
        self.assertEqual((reply['content'], reply['sender_type']), ('Happy to help!', 'USER'))

        # the customer comes back later (new session from a fresh assertion) and finds the same conversation/history
        token2 = await database_sync_to_async(self.customer_session)(info, 'org-a', 'alice', origin)
        again = (await database_sync_to_async(lambda: APIClient().post(
            '/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=token2))()).json()
        self.assertEqual(again['id'], conv['id'])
        history = await database_sync_to_async(lambda: APIClient().get(
            f'/api/v1/widget/conversations/{conv["id"]}/messages/', HTTP_X_WIDGET_SESSION=token2).json())()
        self.assertEqual([m['content'] for m in history], ['I have a question about my order.', 'Happy to help!'])
        await customer.disconnect()
        await operator.disconnect()

    async def test_mode_b_one_question_and_mode_c_structured_form_with_guest(self):
        origin = 'https://org-b.acme.example'
        one = await database_sync_to_async(self.provision)('org-b', 'Beta School', ONE_QUESTION)
        form = await database_sync_to_async(self.provision)('org-c', 'Gamma College', STRUCTURED)
        operator_b = await database_sync_to_async(self.staff_session)('org-b', 'bea', 'admin')
        operator_c = await database_sync_to_async(self.staff_session)('org-c', 'cy', 'admin')

        # Mode B (one question) as a guest: refused without an answer, accepted with one
        guest = await database_sync_to_async(self.guest_session)(one, origin)

        def start(token, **body):
            return APIClient().post('/api/v1/widget/start/', body, format='json', HTTP_X_WIDGET_SESSION=token)
        refused = await database_sync_to_async(start)(guest)
        self.assertEqual((refused.status_code, refused.json()['code']), (400, 'pre_chat_invalid'))
        self.assertEqual(await database_sync_to_async(Conversation.objects.count)(), 0)
        ok = (await database_sync_to_async(start)(guest, pre_chat={'help': 'Where is my certificate?'})).json()
        ctx = await database_sync_to_async(lambda: operator_b.get(
            f'/api/v1/conversations/customer/{ok["id"]}/customer-context/').json())()
        self.assertEqual([(a['label'], a['value']) for a in ctx['pre_chat']], [('What can we help you with?', 'Where is my certificate?')])
        self.assertFalse(ctx['identity_verified'])

        # Mode C (structured form) as a guest
        guest_c = await database_sync_to_async(self.guest_session)(form, 'https://org-c.acme.example')
        bad = await database_sync_to_async(start)(guest_c, pre_chat={'topic': 'refund', 'agree': True})
        self.assertEqual(bad.status_code, 400)
        good = (await database_sync_to_async(start)(guest_c, pre_chat={'topic': 'billing', 'agree': True})).json()
        ctx_c = await database_sync_to_async(lambda: operator_c.get(
            f'/api/v1/conversations/customer/{good["id"]}/customer-context/').json())()
        self.assertEqual({a['key']: a['value'] for a in ctx_c['pre_chat']}, {'topic': 'billing', 'agree': True})

        # tenant isolation: org-b's operator can neither list nor open org-c's conversation
        listed = await database_sync_to_async(lambda: operator_b.get('/api/v1/conversations/customer/').json())()
        self.assertEqual([c['id'] for c in listed], [ok['id']])
        denied = await database_sync_to_async(lambda: operator_b.get(f'/api/v1/conversations/{good["id"]}/messages/'))()
        self.assertEqual(denied.status_code, 404)
        ticket = await database_sync_to_async(lambda: operator_b.post(
            '/api/v1/ws/ticket/', {'kind': 'dashboard_chat', 'conversation_id': good['id']}, format='json'))()
        self.assertEqual(ticket.status_code, 404)

    async def test_deprovisioning_cuts_off_staff_and_customers_while_history_stays(self):
        origin = 'https://org-a.acme.example'
        info = await database_sync_to_async(self.provision)('org-a', 'Acme Academy', {})
        token = await database_sync_to_async(self.customer_session)(info, 'org-a', 'alice', origin)
        conv = (await database_sync_to_async(lambda: APIClient().post(
            '/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=token))()).json()
        operator_api = await database_sync_to_async(self.staff_session)('org-a', 'bob', 'operator')
        operator = await self.operator_socket(operator_api, conv['id'])
        customer = await self.widget_socket(token, conv['id'])

        # the host removes bob and suspends the tenant
        removed = await database_sync_to_async(lambda: self.host.call(
            APIClient(), 'delete', TENANT.format('org-a') + 'members/bob/'))()
        self.assertEqual(removed.json(), {'removed': True})
        await operator.send_json_to({'message': 'still here?', 'client_message_id': 'o2'})
        out = await operator.receive_output(timeout=3)             # live revalidation closes the removed member's socket
        self.assertEqual(out['type'], 'websocket.close')
        self.assertEqual(
            (await database_sync_to_async(lambda: operator_api.get('/api/v1/conversations/customer/'))()).status_code, 403)

        await database_sync_to_async(lambda: self.host.call(APIClient(), 'put', TENANT.format('org-a'), {'status': 'suspended'}))()
        await customer.send_json_to({'message': 'hello?', 'client_message_id': 'c9'})
        out = await customer.receive_output(timeout=3)
        self.assertEqual(out['type'], 'websocket.close')
        again = await database_sync_to_async(lambda: APIClient().post(
            '/api/v1/widget/start/', {}, format='json', HTTP_X_WIDGET_SESSION=token))()
        self.assertEqual(again.status_code, 401)
        self.assertTrue(await database_sync_to_async(Conversation.objects.filter(pk=conv['id']).exists)())
        self.assertEqual(await database_sync_to_async(Message.objects.filter(conversation_id=conv['id']).count)(), 0)
