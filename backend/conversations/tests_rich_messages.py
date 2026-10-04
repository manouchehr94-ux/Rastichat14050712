from django.test import TransactionTestCase
from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation, Message, MessageReceipt
from catalog.models import Product
from rest_framework.test import APIClient
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async
from config.asgi import application
from django.core.files.uploadedfile import SimpleUploadedFile
import asyncio


class RichMessageFlowTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project, name='Sara')
        self.session = VisitorSession.objects.create(visitor=self.visitor)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.token = res.data['access']

    def test_operator_can_upload_image(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        img = SimpleUploadedFile('pic.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 16, content_type='image/png')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/upload/',
            {'file': img, 'message_type': 'IMAGE', 'client_message_id': 'img1', 'caption': 'nice'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['message_type'], 'IMAGE')
        self.assertIsNotNone(res.data['attachment_url'])
        self.assertEqual(res.data['metadata']['caption'], 'nice')

    def test_visitor_can_upload_voice(self):
        audio = SimpleUploadedFile('note.webm', b'\x1a\x45\xdf\xa3' + b'\x00' * 16, content_type='audio/webm')
        res = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/upload/',
            {'file': audio, 'message_type': 'VOICE', 'client_message_id': 'v1', 'duration': '5', 'session_token': str(self.session.token)},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['message_type'], 'VOICE')
        self.assertEqual(res.data['metadata']['duration'], '5')

    def test_operator_can_share_product(self):
        product = Product.objects.create(workspace=self.workspace, name='Candle', brand='Arom', price=890000)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/share_product/',
            {'product_id': str(product.id), 'client_message_id': 'p1'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['message_type'], 'PRODUCT')
        self.assertEqual(res.data['metadata']['name'], 'Candle')

    def test_cannot_share_product_from_other_workspace(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        other_product = Product.objects.create(workspace=other_ws, name='Foreign', brand='X', price=1000)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/share_product/',
            {'product_id': str(other_product.id), 'client_message_id': 'p2'},
            format='json',
        )
        self.assertEqual(res.status_code, 404)

    def test_operator_can_request_rating_and_visitor_can_rate(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/request_rating/', format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['message_type'], 'RATING_REQUEST')

        res2 = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/rate/',
            {'session_token': str(self.session.token), 'rating': 5},
            format='json',
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['rating'], 5)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.rating, 5)

    def test_invalid_rating_rejected(self):
        res = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/rate/',
            {'session_token': str(self.session.token), 'rating': 9},
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_widget_can_fetch_message_history(self):
        Message.objects.create(conversation=self.conv, sender_type='USER', sender=self.operator, content='Hi there', client_message_id='h1')
        res = self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/?session_token={self.session.token}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['content'], 'Hi there')

    def test_widget_history_rejects_wrong_session(self):
        res = self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/?session_token=00000000-0000-0000-0000-000000000000')
        self.assertEqual(res.status_code, 404)

    def test_operator_mark_read_creates_receipts_and_seen_flag(self):
        msg = Message.objects.create(conversation=self.conv, sender_type='VISITOR', sender_visitor=self.visitor, content='Hey', client_message_id='m1')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/mark_read/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(MessageReceipt.objects.filter(message=msg, user=self.operator).exists())

    def test_visitor_mark_read_marks_operator_messages_seen(self):
        msg = Message.objects.create(conversation=self.conv, sender_type='USER', sender=self.operator, content='Hi', client_message_id='m2')
        res = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/mark_read/',
            {'session_token': str(self.session.token)}, format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(MessageReceipt.objects.filter(message=msg, visitor=self.visitor).exists())
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        history = self.client.get(f'/api/v1/conversations/{self.conv.id}/messages/')
        self.assertTrue(history.data[0]['seen'])

    def test_product_snapshot_survives_later_product_edit(self):
        product = Product.objects.create(workspace=self.workspace, name='Candle', brand='Arom', price=890000)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/share_product/',
            {'product_id': str(product.id), 'client_message_id': 'snap1'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)

        product.name = 'Renamed Candle'
        product.price = 500000
        product.save(update_fields=['name', 'price'])

        msg = Message.objects.get(client_message_id='snap1')
        self.assertEqual(msg.metadata['name'], 'Candle')
        self.assertEqual(msg.metadata['price'], '890000')

    def test_product_list_scoped_to_workspace(self):
        Product.objects.create(workspace=self.workspace, name='Mine', brand='Arom', price=1000)
        other_platform = Platform.objects.create(name='P3')
        other_ws = Workspace.objects.create(name='WS3', platform=other_platform)
        Product.objects.create(workspace=other_ws, name='NotMine', brand='X', price=1000)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.get('/api/v1/products/')
        self.assertEqual(res.status_code, 200)
        names = [p['name'] for p in res.data]
        self.assertIn('Mine', names)
        self.assertNotIn('NotMine', names)


class TypingAndSeenWebsocketTests(TransactionTestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op2@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project)
        self.session = VisitorSession.objects.create(visitor=self.visitor)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'op2@test.com', 'password': 'pass1234'}, format='json')
        self.token = res.data['access']

    async def test_typing_indicator_reaches_other_side_only(self):
        vis_comm = WebsocketCommunicator(application, f"/ws/widget/{self.session.token}/{self.conv.id}/")
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token}/{self.conv.id}/")
        await vis_comm.connect(); await op_comm.connect()

        await vis_comm.send_json_to({'type': 'typing'})
        response = await op_comm.receive_json_from(timeout=2)
        self.assertEqual(response['type'], 'typing')
        self.assertEqual(response['sender_type'], 'VISITOR')

        # visitor's own typing event should not be echoed back to itself
        self.assertTrue(await vis_comm.receive_nothing(timeout=0.3))
        await vis_comm.disconnect(); await op_comm.disconnect()

    async def test_rest_send_broadcasts_to_live_socket_without_error(self):
        # Regression test: broadcasting a DRF-serialized message over the channel
        # layer previously 500'd whenever a live subscriber was in the group,
        # because `conversation` serializes as a raw UUID (msgpack can't pack it).
        # An empty group hides this bug since group_send skips serialization
        # when there is nothing to deliver to, so a connected socket is required
        # to actually exercise the serialization path.
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token}/{self.conv.id}/")
        await op_comm.connect()

        await database_sync_to_async(self.client.credentials)(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = await database_sync_to_async(self.client.post)(
            f'/api/v1/conversations/{self.conv.id}/send/',
            {'content': 'Hello from REST', 'client_message_id': 'rest1'},
            format='json',
        )
        self.assertEqual(res.status_code, 201)

        response = await op_comm.receive_json_from(timeout=2)
        self.assertEqual(response['content'], 'Hello from REST')
        await op_comm.disconnect()

    async def test_mark_read_broadcasts_seen(self):
        vis_comm = WebsocketCommunicator(application, f"/ws/widget/{self.session.token}/{self.conv.id}/")
        op_comm = WebsocketCommunicator(application, f"/ws/dashboard/{self.token}/{self.conv.id}/")
        await vis_comm.connect(); await op_comm.connect()

        await op_comm.send_json_to({'message': 'Hello', 'client_message_id': 'x1'})
        await vis_comm.receive_json_from(timeout=2)
        await op_comm.receive_json_from(timeout=2)  # operator's own message is echoed back to them too

        await vis_comm.send_json_to({'type': 'mark_read'})
        response = await op_comm.receive_json_from(timeout=2)
        self.assertEqual(response['type'], 'message.seen')
        self.assertEqual(response['reader'], 'VISITOR')

        await database_sync_to_async(self.client.credentials)(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        msg = await database_sync_to_async(Message.objects.get)(client_message_id='x1')
        self.assertTrue(await database_sync_to_async(MessageReceipt.objects.filter(message=msg, visitor=self.visitor).exists)())

        await vis_comm.disconnect(); await op_comm.disconnect()
