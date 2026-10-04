from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import User, OperatorPresence
from .presence import touch_presence


class OperatorPresenceModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='op@test.com', password='pass1234')

    def test_first_touch_defaults_to_online(self):
        presence = touch_presence(self.user)
        self.assertEqual(presence.status, OperatorPresence.Status.ONLINE)
        self.assertEqual(presence.effective_status(), OperatorPresence.Status.ONLINE)

    def test_explicit_offline_is_not_overridden_by_activity_touch(self):
        touch_presence(self.user, explicit_status=OperatorPresence.Status.OFFLINE)
        presence = touch_presence(self.user)  # plain activity touch, no explicit status
        self.assertEqual(presence.status, OperatorPresence.Status.OFFLINE)
        self.assertEqual(presence.effective_status(), OperatorPresence.Status.OFFLINE)

    def test_stale_online_decays_to_away_then_offline(self):
        presence = touch_presence(self.user)
        # `last_active_at` is `auto_now=True`, so it always resets to "now" on
        # any Model.save() — a queryset-level .update() is required to
        # actually backdate it for this test, bypassing that auto_now logic.
        OperatorPresence.objects.filter(pk=presence.pk).update(last_active_at=timezone.now() - timedelta(minutes=5))
        presence.refresh_from_db()
        self.assertEqual(presence.effective_status(), OperatorPresence.Status.AWAY)

        OperatorPresence.objects.filter(pk=presence.pk).update(last_active_at=timezone.now() - timedelta(minutes=20))
        presence.refresh_from_db()
        self.assertEqual(presence.effective_status(), OperatorPresence.Status.OFFLINE)

    def test_explicit_online_still_decays_when_stale(self):
        presence = touch_presence(self.user, explicit_status=OperatorPresence.Status.ONLINE)
        OperatorPresence.objects.filter(pk=presence.pk).update(last_active_at=timezone.now() - timedelta(minutes=20))
        presence.refresh_from_db()
        # A stored "ONLINE" claim from long ago must not be trusted at read time.
        self.assertEqual(presence.effective_status(), OperatorPresence.Status.OFFLINE)


class PresenceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='op2@test.com', password='pass1234')
        res = self.client.post('/api/v1/auth/login/', {'email': 'op2@test.com', 'password': 'pass1234'}, format='json')
        self.token = res.data['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def test_set_status_requires_authentication(self):
        anon = APIClient()
        res = anon.post('/api/v1/auth/presence/', {'status': 'ONLINE'}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_set_and_read_own_status(self):
        res = self.client.post('/api/v1/auth/presence/', {'status': 'AWAY'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'AWAY')

    def test_invalid_status_rejected(self):
        res = self.client.post('/api/v1/auth/presence/', {'status': 'NOT_A_STATUS'}, format='json')
        self.assertEqual(res.status_code, 400)
