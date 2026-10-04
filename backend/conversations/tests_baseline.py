from django.test import TransactionTestCase
from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation, Message
from rest_framework.test import APIClient
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from config.asgi import application
import uuid
import asyncio

class BaselineSecurityTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform_a = Platform.objects.create(name='PA')
        self.ws_a = Workspace.objects.create(name='WA', platform=self.platform_a)
        self.proj_a = Project.objects.create(name='PrA', workspace=self.ws_a)
        self.op_a = User.objects.create_user(email='opa@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_a, workspace=self.ws_a, role='WORKSPACE_OPERATOR')
        self.vis_a = Visitor.objects.create(project=self.proj_a)
        self.sess_a = VisitorSession.objects.create(visitor=self.vis_a)
        
        self.platform_b = Platform.objects.create(name='PB')
        self.ws_b = Workspace.objects.create(name='WB', platform=self.platform_b)
        self.proj_b = Project.objects.create(name='PrB', workspace=self.ws_b)
        self.op_b = User.objects.create_user(email='opb@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.op_b, workspace=self.ws_b, role='WORKSPACE_OPERATOR')
        self.vis_b = Visitor.objects.create(project=self.proj_b)
        self.sess_b = VisitorSession.objects.create(visitor=self.vis_b)

        self.conv_a = Conversation.objects.create(visitor=self.vis_a, workspace=self.ws_a, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN)
        self.conv_b = Conversation.objects.create(visitor=self.vis_b, workspace=self.ws_b, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN)

        res_a = self.client.post('/api/v1/auth/login/', {'email': 'opa@test.com', 'password': 'pass1234'}, format='json')
        self.token_a = res_a.data['access']
        res_b = self.client.post('/api/v1/auth/login/', {'email': 'opb@test.com', 'password': 'pass1234'}, format='json')
        self.token_b = res_b.data['access']

    def test_1_visitor_start_chat(self):
        res = self.client.post('/api/v1/widget/start/', {'session_token': str(self.sess_a.token)}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['id'], str(self.conv_a.id))

    async def test_2_visitor_can_send_message(self):
        communicator = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.send_json_to({'message': 'Hello', 'client_message_id': 'v1'})
        await asyncio.sleep(0.1)
        self.assertTrue(await database_sync_to_async(Message.objects.filter(client_message_id='v1').exists)())
        await communicator.disconnect()

    async def test_3_operator_receives_message(self):
        vis_comm = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token_a}/{self.conv_a.id}/")
        await vis_comm.connect(); await op_comm.connect()
        await vis_comm.send_json_to({'message': 'Hi Op', 'client_message_id': 'v2'})
        response = await op_comm.receive_json_from(timeout=2)
        self.assertEqual(response['content'], 'Hi Op')
        await vis_comm.disconnect(); await op_comm.disconnect()

    async def test_4_operator_can_reply(self):
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token_a}/{self.conv_a.id}/")
        await op_comm.connect()
        await op_comm.send_json_to({'message': 'Hi Vis', 'client_message_id': 'o1'})
        await asyncio.sleep(0.1)
        self.assertTrue(await database_sync_to_async(Message.objects.filter(client_message_id='o1').exists)())
        await op_comm.disconnect()

    async def test_5_visitor_receives_reply(self):
        vis_comm = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token_a}/{self.conv_a.id}/")
        await vis_comm.connect(); await op_comm.connect()
        await op_comm.send_json_to({'message': 'Reply', 'client_message_id': 'o2'})
        while True:
            response = await vis_comm.receive_json_from(timeout=2)
            if response['sender_type'] == 'USER': break
        self.assertEqual(response['content'], 'Reply')
        await vis_comm.disconnect(); await op_comm.disconnect()

    def test_6_history_survives_refresh(self):
        Message.objects.create(conversation=self.conv_a, sender_type='USER', sender=self.op_a, content='History', client_message_id='h1')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        res = self.client.get(f'/api/v1/conversations/{self.conv_a.id}/messages/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['content'], 'History')

    def test_7_anonymous_visitor_rejected(self):
        res = self.client.post('/api/v1/widget/start/', {'session_token': 'invalid-token'}, format='json')
        self.assertEqual(res.status_code, 401)

    async def test_8_visitor_a_cannot_access_visitor_b(self):
        communicator = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_b.id}/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_9_cross_project_ws_denied(self):
        communicator = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_b.id}/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    def test_10_cross_workspace_operator_denied(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_b}')
        res = self.client.get(f'/api/v1/conversations/{self.conv_a.id}/messages/')
        self.assertEqual(res.status_code, 404)

    async def test_11_unauth_ws_rejected(self):
        communicator = WebsocketCommunicator(application, f"/ws/dashboard/invalid-token/{uuid.uuid4()}/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    def test_12_client_sender_id_ignored(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        res = self.client.post(f'/api/v1/conversations/{self.conv_a.id}/send/', {
            'content': 'Test', 'client_message_id': 'c1', 'sender_id': 999
        }, format='json')
        self.assertEqual(res.status_code, 201)
        msg = Message.objects.get(client_message_id='c1')
        self.assertEqual(msg.sender_id, self.op_a.id)

    def test_13_operator_cannot_impersonate(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        res = self.client.post(f'/api/v1/conversations/{self.conv_a.id}/send/', {
            'content': 'Test', 'client_message_id': 'c2', 'sender_id': self.op_b.id
        }, format='json')
        msg = Message.objects.get(client_message_id='c2')
        self.assertEqual(msg.sender_id, self.op_a.id)

    async def test_14_visitor_cannot_impersonate(self):
        communicator = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        await communicator.connect()
        await communicator.send_json_to({'message': 'Impersonate', 'client_message_id': 'v3', 'sender_visitor_id': self.vis_b.id})
        await asyncio.sleep(0.1)
        msg = await database_sync_to_async(Message.objects.get)(client_message_id='v3')
        self.assertEqual(msg.sender_visitor_id, self.vis_a.id)
        await communicator.disconnect()

    def test_15_invalid_conv_uuid_handled(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        res = self.client.get(f'/api/v1/conversations/invalid-uuid/messages/')
        self.assertEqual(res.status_code, 404)

    def test_16_empty_message_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        res = self.client.post(f'/api/v1/conversations/{self.conv_a.id}/send/', {
            'content': '   ', 'client_message_id': 'c3'
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_17_long_message_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        res = self.client.post(f'/api/v1/conversations/{self.conv_a.id}/send/', {
            'content': 'a' * 5001, 'client_message_id': 'c4'
        }, format='json')
        self.assertEqual(res.status_code, 400)

    async def test_18_malformed_ws_json_handled(self):
        communicator = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        await communicator.connect()
        await communicator.send_to(text_data="not a json")
        await communicator.send_json_to({'message': 'Recover', 'client_message_id': 'v4'})
        await asyncio.sleep(0.1)
        self.assertTrue(await database_sync_to_async(Message.objects.filter(client_message_id='v4').exists)())
        await communicator.disconnect()

    def test_19_duplicate_idempotency(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_a}')
        self.client.post(f'/api/v1/conversations/{self.conv_a.id}/send/', {'content': 'Msg1', 'client_message_id': 'dup1'}, format='json')
        self.client.post(f'/api/v1/conversations/{self.conv_a.id}/send/', {'content': 'Msg1', 'client_message_id': 'dup1'}, format='json')
        self.assertEqual(Message.objects.filter(client_message_id='dup1').count(), 1)

    async def test_20_reconnect_no_duplicates(self):
        comm1 = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        await comm1.connect()
        await comm1.send_json_to({'message': 'Msg1', 'client_message_id': 'rc1'})
        await comm1.disconnect()
        
        comm2 = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        await comm2.connect()
        await comm2.send_json_to({'message': 'Msg1', 'client_message_id': 'rc1'})
        await comm2.disconnect()
        
        await asyncio.sleep(0.1)
        count = await database_sync_to_async(Message.objects.filter(client_message_id='rc1').count)()
        self.assertEqual(count, 1)

    async def test_e2e_customer_chat_flow(self):
        vis_comm = WebsocketCommunicator(application, f"/ws/widget/{self.sess_a.token}/{self.conv_a.id}/")
        connected_vis, _ = await vis_comm.connect()
        self.assertTrue(connected_vis)
        
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token_a}/{self.conv_a.id}/")
        connected_op, _ = await op_comm.connect()
        self.assertTrue(connected_op)
        
        unique_msg = "E2E_Visitor_" + str(uuid.uuid4())
        await vis_comm.send_json_to({'message': unique_msg, 'client_message_id': 'e2e_v'})
        
        response = await op_comm.receive_json_from(timeout=2)
        self.assertEqual(response['content'], unique_msg)
        self.assertEqual(response['sender_type'], 'VISITOR')
        
        unique_reply = "E2E_Operator_" + str(uuid.uuid4())
        await op_comm.send_json_to({'message': unique_reply, 'client_message_id': 'e2e_o'})
        
        while True:
            response = await vis_comm.receive_json_from(timeout=2)
            if response['sender_type'] == 'USER': break
        
        self.assertEqual(response['content'], unique_reply)
        
        await asyncio.sleep(0.1)
        v_msg = await database_sync_to_async(Message.objects.get)(client_message_id='e2e_v')
        self.assertEqual(v_msg.sender_visitor_id, self.vis_a.id)
        self.assertIsNone(v_msg.sender_id)
        
        o_msg = await database_sync_to_async(Message.objects.get)(client_message_id='e2e_o')
        self.assertEqual(o_msg.sender_id, self.op_a.id)
        self.assertIsNone(o_msg.sender_visitor_id)
        
        self.assertEqual(await database_sync_to_async(Message.objects.filter(conversation=self.conv_a).count)(), 2)
        
        await vis_comm.disconnect()
        await op_comm.disconnect()
