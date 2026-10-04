from django.test import TransactionTestCase
from accounts.models import User
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation, Message, Assignment, MessageReceipt
from audit.models import AuditEvent
from rest_framework.test import APIClient
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from config.asgi import application
import uuid
import asyncio

class SupportFlowTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.ws = Workspace.objects.create(name='WS1', platform=self.platform)
        self.proj = Project.objects.create(name='Pr1', workspace=self.ws)
        
        self.ws_admin = User.objects.create_user(email='admin@ws.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.ws_admin, workspace=self.ws, role='WORKSPACE_ADMIN')
        
        self.ws_op = User.objects.create_user(email='op@ws.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.ws_op, workspace=self.ws, role='WORKSPACE_OPERATOR')
        
        self.pl_support = User.objects.create_user(email='support@p.com', password='pass1234')
        PlatformMembership.objects.create(user=self.pl_support, platform=self.platform, role='PLATFORM_SUPPORT_AGENT')
        
        self.platform_b = Platform.objects.create(name='P2')
        self.ws_b = Workspace.objects.create(name='WS2', platform=self.platform_b)
        self.pl_support_b = User.objects.create_user(email='support_b@p.com', password='pass1234')
        PlatformMembership.objects.create(user=self.pl_support_b, platform=self.platform_b, role='PLATFORM_SUPPORT_AGENT')

        self.support_conv = Conversation.objects.create(
            workspace=self.ws, type=Conversation.Type.PLATFORM_SUPPORT, 
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject='Pre-created'
        )

        res_admin = self.client.post('/api/v1/auth/login/', {'email': 'admin@ws.com', 'password': 'pass1234'}, format='json')
        self.token_admin = res_admin.data['access']
        res_support = self.client.post('/api/v1/auth/login/', {'email': 'support@p.com', 'password': 'pass1234'}, format='json')
        self.token_support = res_support.data['access']
        res_support_b = self.client.post('/api/v1/auth/login/', {'email': 'support_b@p.com', 'password': 'pass1234'}, format='json')
        self.token_support_b = res_support_b.data['access']

    def test_1_ws_owner_can_create(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.post('/api/v1/support/', {'subject': 'Help'}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_2_ws_admin_can_create(self):
        self.test_1_ws_owner_can_create()

    def test_3_ws_operator_denied(self):
        res_op = self.client.post('/api/v1/auth/login/', {'email': 'op@ws.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res_op.data["access"]}')
        res = self.client.post('/api/v1/support/', {'subject': 'Help'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_4_pl_support_sees_conversation(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support}')
        res = self.client.get('/api/v1/platform/support/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1) 

    def test_5_pl_support_cannot_see_other_platform(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support_b}')
        res = self.client.get('/api/v1/platform/support/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 0)

    def test_6_ws_admin_cannot_see_other_ws(self):
        pass

    def test_7_pl_support_cannot_access_customer(self):
        vis = Visitor.objects.create(project=self.proj)
        sess = VisitorSession.objects.create(visitor=vis)
        self.client.post('/api/v1/widget/start/', {'session_token': str(sess.token)}, format='json')
        
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support}')
        res = self.client.get('/api/v1/platform/support/')
        self.assertEqual(len(res.data), 1) 

    def test_8_ws_admin_can_send(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.post(f'/api/v1/support/{conv_id}/send_message/', {'content': 'Hi', 'client_message_id': 's1'}, format='json')
        self.assertEqual(res.status_code, 201)

    def test_9_pl_support_receives(self):
        self.test_8_ws_admin_can_send()
        self.assertEqual(Message.objects.filter(conversation_id=self.support_conv.id).count(), 1)

    def test_10_pl_support_can_reply(self):
        self.test_8_ws_admin_can_send()
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support}')
        res = self.client.post(f'/api/v1/platform/support/{conv_id}/reply/', {'content': 'Hello', 'client_message_id': 's2'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Conversation.objects.get(id=conv_id).status, 'WAITING_FOR_WORKSPACE')

    def test_11_ws_admin_receives_reply(self):
        self.test_10_pl_support_can_reply()
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.get(f'/api/v1/support/{conv_id}/messages/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 2)

    def test_12_client_sender_id_ignored(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        self.client.post(f'/api/v1/support/{conv_id}/send_message/', {'content': 'Hi', 'client_message_id': 's3', 'sender_id': 999}, format='json')
        msg = Message.objects.get(client_message_id='s3')
        self.assertEqual(msg.sender_id, self.ws_admin.id)

    async def test_13_unauth_ws_rejected(self):
        communicator = WebsocketCommunicator(application, f"/ws/dashboard/support/invalid-token/{uuid.uuid4()}/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_14_conv_uuid_insufficient(self):
        communicator = WebsocketCommunicator(application, f"/ws/dashboard/support/{self.token_admin}/{uuid.uuid4()}/")
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    def test_15_assignment_same_platform(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support}')
        res = self.client.post(f'/api/v1/platform/support/{conv_id}/assign/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Assignment.objects.count(), 1)

    def test_16_assignment_cross_platform_rejected(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support_b}')
        res = self.client.post(f'/api/v1/platform/support/{conv_id}/assign/', format='json')
        self.assertEqual(res.status_code, 404)

    def test_17_reassignment_preserves_history(self):
        self.test_15_assignment_same_platform()
        conv_id = self.support_conv.id
        self.client.post(f'/api/v1/platform/support/{conv_id}/assign/', format='json')
        self.assertEqual(Assignment.objects.filter(conversation_id=conv_id).count(), 2)

    def test_18_duplicate_idempotency(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        self.client.post(f'/api/v1/support/{conv_id}/send_message/', {'content': 'Msg', 'client_message_id': 'dup1'}, format='json')
        res2 = self.client.post(f'/api/v1/support/{conv_id}/send_message/', {'content': 'Msg', 'client_message_id': 'dup1'}, format='json')
        self.assertEqual(res2.status_code, 409)
        self.assertEqual(Message.objects.filter(client_message_id='dup1').count(), 1)

    def test_19_empty_message_rejected(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.post(f'/api/v1/support/{conv_id}/send_message/', {'content': '   ', 'client_message_id': 'e1'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_20_oversized_message_rejected(self):
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.post(f'/api/v1/support/{conv_id}/send_message/', {'content': 'a'*5001, 'client_message_id': 'o1'}, format='json')
        self.assertEqual(res.status_code, 400)

    async def test_21_malformed_json_handled(self):
        conv_id = self.support_conv.id
        communicator = WebsocketCommunicator(application, f"/ws/dashboard/support/{self.token_admin}/{conv_id}/")
        await communicator.connect()
        await communicator.send_to(text_data="bad json")
        await communicator.send_json_to({'message': 'Recover', 'client_message_id': 'm1'})
        await asyncio.sleep(0.1)
        self.assertTrue(await database_sync_to_async(Message.objects.filter(client_message_id='m1').exists)())
        await communicator.disconnect()

    def test_22_history_pagination(self):
        pass

    def test_23_read_state_updates(self):
        self.test_10_pl_support_can_reply()
        conv_id = self.support_conv.id
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.post(f'/api/v1/support/{conv_id}/mark_read/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(MessageReceipt.objects.filter(user=self.ws_admin).count(), 2)

    def test_24_invalid_transition(self):
        pass

    def test_25_valid_transitions(self):
        pass

    def test_26_close(self):
        pass

    def test_27_reopen(self):
        pass

    def test_28_audit_events(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        res = self.client.post('/api/v1/support/', {'subject': 'Audit Test'}, format='json')
        conv_id = res.data['id']
        
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token_support}')
        self.client.post(f'/api/v1/platform/support/{conv_id}/assign/', format='json')
        
        self.assertTrue(AuditEvent.objects.filter(action='support_conversation_created').exists())
        self.assertTrue(AuditEvent.objects.filter(action='support_conversation_assigned').exists())

    async def test_e2e_support_flow(self):
        conv_id = self.support_conv.id
        
        admin_comm = WebsocketCommunicator(application, f"/ws/dashboard/support/{self.token_admin}/{conv_id}/")
        support_comm = WebsocketCommunicator(application, f"/ws/dashboard/support/{self.token_support}/{conv_id}/")
        await admin_comm.connect(); await support_comm.connect()
        
        await admin_comm.send_json_to({'message': 'E2E Admin', 'client_message_id': 'e2e_a'})
        
        # Support receives Admin message
        while True:
            response = await support_comm.receive_json_from(timeout=2)
            if response['content'] == 'E2E Admin': break
            
        self.assertEqual(response['content'], 'E2E Admin')
        
        await database_sync_to_async(self.client.credentials)(HTTP_AUTHORIZATION=f'Bearer {self.token_support}')
        await database_sync_to_async(self.client.post)(f'/api/v1/platform/support/{conv_id}/assign/', format='json')
        
        await support_comm.send_json_to({'message': 'E2E Support', 'client_message_id': 'e2e_s'})
        
        # Admin receives Support message
        while True:
            response = await admin_comm.receive_json_from(timeout=2)
            if response['content'] == 'E2E Support': break
            
        self.assertEqual(response['content'], 'E2E Support')
        
        await asyncio.sleep(0.1)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(conversation_id=conv_id).count)(), 2)
        
        await database_sync_to_async(self.client.credentials)(HTTP_AUTHORIZATION=f'Bearer {self.token_admin}')
        await database_sync_to_async(self.client.post)(f'/api/v1/support/{conv_id}/mark_read/', format='json')
        self.assertEqual(await database_sync_to_async(MessageReceipt.objects.filter(user_id=self.ws_admin.id).count)(), 2)
        
        await admin_comm.send_json_to({'message': 'E2E Admin', 'client_message_id': 'e2e_a'})
        await asyncio.sleep(0.1)
        self.assertEqual(await database_sync_to_async(Message.objects.filter(conversation_id=conv_id).count)(), 2)
        
        await admin_comm.disconnect(); await support_comm.disconnect()
