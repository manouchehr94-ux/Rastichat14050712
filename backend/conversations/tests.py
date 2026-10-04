from django.test import TestCase
from accounts.models import User
from platforms.models import Platform, PlatformMembership
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation, Message
from rest_framework.test import APIClient
import uuid

class CustomerChatFlowTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='operator@test.com', password='pass1234')
        self.platform = Platform.objects.create(name='TestPlatform')
        self.workspace = Workspace.objects.create(name='TestWS', platform=self.platform)
        WorkspaceMembership.objects.create(user=self.user, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.project = Project.objects.create(name='TestProj', workspace=self.workspace)
        self.visitor = Visitor.objects.create(project=self.project)
        self.session = VisitorSession.objects.create(visitor=self.visitor)
        self.client = APIClient()

    def test_visitor_start_chat_and_operator_reply(self):
        res = self.client.post('/api/v1/widget/start/', {'session_token': str(self.session.token)}, format='json')
        self.assertEqual(res.status_code, 200)
        conv_id = res.data['id']
        self.assertEqual(res.data['status'], 'OPEN')
        
        login_res = self.client.post('/api/v1/auth/login/', {'email': 'operator@test.com', 'password': 'pass1234'}, format='json')
        self.assertEqual(login_res.status_code, 200)
        token = login_res.data['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        
        convs_res = self.client.get('/api/v1/conversations/customer/')
        self.assertEqual(convs_res.status_code, 200)
        self.assertEqual(len(convs_res.data), 1)
        
        msg_res = self.client.post(f'/api/v1/conversations/{conv_id}/send/', {'content': 'Hello', 'client_message_id': 'msg1'}, format='json')
        self.assertEqual(msg_res.status_code, 201)
        
        dup_res = self.client.post(f'/api/v1/conversations/{conv_id}/send/', {'content': 'Hello', 'client_message_id': 'msg1'}, format='json')
        self.assertEqual(dup_res.status_code, 409)
        
        close_res = self.client.post(f'/api/v1/conversations/customer/{conv_id}/close/', format='json')
        self.assertEqual(close_res.status_code, 200)
        self.assertEqual(close_res.data['status'], 'CLOSED')

class PlatformSupportFlowTest(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.ws_admin = User.objects.create_user(email='admin@ws.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.ws_admin, workspace=self.workspace, role='WORKSPACE_ADMIN')
        self.pl_support = User.objects.create_user(email='support@platform.com', password='pass1234')
        PlatformMembership.objects.create(user=self.pl_support, platform=self.platform, role='PLATFORM_SUPPORT_AGENT')
        self.client = APIClient()

    def test_support_flow(self):
        # 1. Workspace Admin logs in and creates support ticket
        login_res = self.client.post('/api/v1/auth/login/', {'email': 'admin@ws.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {login_res.data["access"]}')
        
        create_res = self.client.post('/api/v1/support/', {'subject': 'Need help'}, format='json')
        self.assertEqual(create_res.status_code, 201)
        self.assertEqual(create_res.data['status'], 'WAITING_FOR_PLATFORM')
        conv_id = create_res.data['id']
        
        # 2. Platform Support logs in and sees ticket
        login_res2 = self.client.post('/api/v1/auth/login/', {'email': 'support@platform.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {login_res2.data["access"]}')
        
        list_res = self.client.get('/api/v1/platform/support/')
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.data), 1)
        
        # 3. Platform Support assigns and replies
        assign_res = self.client.post(f'/api/v1/platform/support/{conv_id}/assign/', format='json')
        self.assertEqual(assign_res.status_code, 200)
        self.assertEqual(assign_res.data['status'], 'WAITING_FOR_PLATFORM')
        
        reply_res = self.client.post(f'/api/v1/platform/support/{conv_id}/reply/', {'content': 'We are on it', 'client_message_id': 'sup1'}, format='json')
        self.assertEqual(reply_res.status_code, 201)
