from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from .models import Notification
from .services import notify


class NotificationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.other_operator = User.objects.create_user(email='op2@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.other_operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')

        notify(self.operator, self.workspace, Notification.EventType.CONVERSATION_ASSIGNED, 'گفتگویی واگذار شد')
        notify(self.operator, self.workspace, Notification.EventType.MENTIONED, 'اشاره شدید')
        notify(self.other_operator, self.workspace, Notification.EventType.CONVERSATION_ASSIGNED, 'برای دیگری')

        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')

    def test_list_only_returns_the_recipients_own_notifications(self):
        res = self.client.get('/api/v1/notifications/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['results']), 2)

    def test_unread_count(self):
        res = self.client.get('/api/v1/notifications/unread_count/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 2)

    def test_mark_read_updates_just_that_notification(self):
        notif = Notification.objects.filter(recipient=self.operator).first()
        res = self.client.post(f'/api/v1/notifications/{notif.id}/mark_read/')
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data['read_at'])
        count_res = self.client.get('/api/v1/notifications/unread_count/')
        self.assertEqual(count_res.data['count'], 1)

    def test_mark_all_read(self):
        res = self.client.post('/api/v1/notifications/mark_all_read/')
        self.assertEqual(res.status_code, 200)
        count_res = self.client.get('/api/v1/notifications/unread_count/')
        self.assertEqual(count_res.data['count'], 0)
        # The other operator's notification must be untouched.
        self.assertTrue(Notification.objects.filter(recipient=self.other_operator, read_at__isnull=True).exists())

    def test_cannot_mark_another_users_notification_read(self):
        other_notif = Notification.objects.filter(recipient=self.other_operator).first()
        res = self.client.post(f'/api/v1/notifications/{other_notif.id}/mark_read/')
        self.assertEqual(res.status_code, 404)
