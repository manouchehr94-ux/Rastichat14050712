"""P1-2: customer (visitor) session lifecycle.

Before this change `VisitorSession.expires_at` was never set or checked, so a
leaked token was valid forever over REST and WebSocket and there was no logout
or rotation. These tests pin: default expiry, sliding renewal bounded by an
absolute max age, revocation, rotation, the safe legacy-session migration, and
that an expired/revoked session reaches the customer's history over NO channel.
"""
import importlib
from datetime import timedelta
from io import StringIO

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from config.asgi import application
from conversations.models import Conversation, Message
from knowledge_base.tests_base import KBTestMixin
from visitors.models import Visitor, VisitorSession
from visitors.sessions import effective_expiry, get_valid_session


class _World(KBTestMixin):
    def make_world(self):
        self.ws = self.make_workspace()
        self.project = self.make_project(self.ws)
        self.visitor = self.make_visitor(self.project)
        self.session = self.make_visitor_session(self.visitor)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)
        Message.objects.create(conversation=self.conv, sender_type='VISITOR', sender_visitor=self.visitor,
                               content='private history', client_message_id='m1')
        self.client = APIClient()

    def set_session(self, **fields):
        VisitorSession.objects.filter(id=self.session.id).update(**fields)
        self.session.refresh_from_db()

    def token(self):
        return str(self.session.token)

    def history(self, token=None, **kw):
        return self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/', {'session_token': self.token() if token is None else token}, **kw)

    # every REST channel a visitor session unlocks
    def rest_calls(self, token):
        cid = self.conv.id
        return {
            'start': self.client.post('/api/v1/widget/start/', {'session_token': token}, format='json'),
            'history': self.client.get(f'/api/v1/widget/conversations/{cid}/messages/', {'session_token': token}),
            'branding': self.client.get(f'/api/v1/widget/conversations/{cid}/branding/', {'session_token': token}),
            'mark_read': self.client.post(f'/api/v1/widget/conversations/{cid}/mark_read/', {'session_token': token}, format='json'),
            'rate': self.client.post(f'/api/v1/widget/conversations/{cid}/rate/', {'session_token': token, 'rating': 5}, format='json'),
        }


class SessionExpiryTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_new_sessions_get_an_expiry_from_the_ttl(self):
        res = self.client.post('/api/v1/widget/init/', {'project_key': str(self.project.public_key)}, format='json')
        s = VisitorSession.objects.get(token=res.data['session_token'])
        self.assertIsNotNone(s.expires_at)
        self.assertAlmostEqual((s.expires_at - timezone.now()).days, 30, delta=1)
        self.assertIn('expires_at', res.data)

    def test_valid_session_works_on_every_rest_channel(self):
        for name, res in self.rest_calls(self.token()).items():
            self.assertIn(res.status_code, (200, 201), name)

    def test_expired_session_is_rejected_on_every_rest_channel(self):
        self.set_session(expires_at=timezone.now() - timedelta(seconds=1))
        for name, res in self.rest_calls(self.token()).items():
            self.assertEqual(res.status_code, 401, name)
            self.assertEqual(res.data['code'], 'session_invalid', name)

    def test_expired_session_cannot_read_history(self):
        self.set_session(expires_at=timezone.now() - timedelta(days=1))
        res = self.history()
        self.assertEqual(res.status_code, 401)
        self.assertNotIn('private history', str(res.content))

    def test_expired_session_cannot_vote_on_kb_feedback(self):
        self.set_session(expires_at=timezone.now() - timedelta(days=1))
        res = self.client.post('/api/v1/kb/public/articles/some-slug/feedback/',
                               {'session_token': self.token(), 'is_helpful': True}, format='json')
        self.assertIn(res.status_code, (401, 404))  # never 200/201

    def test_session_token_in_header_is_accepted(self):
        res = self.client.get(f'/api/v1/widget/conversations/{self.conv.id}/messages/',
                              HTTP_X_WIDGET_SESSION=self.token())
        self.assertEqual(res.status_code, 200)
        self.assertEqual([m['content'] for m in res.data], ['private history'])

    def test_unknown_and_garbage_tokens_are_401_not_500(self):
        for bad in ('00000000-0000-0000-0000-000000000000', 'not-a-uuid', ''):
            self.assertEqual(self.history(token=bad).status_code, 401, bad)

    def test_other_visitors_valid_session_still_cannot_read_this_conversation(self):
        other = self.make_visitor_session(self.make_visitor(self.project))
        self.assertEqual(self.history(token=str(other.token)).status_code, 404)

    def test_inactive_project_or_workspace_invalidates_session(self):
        self.project.is_active = False
        self.project.save()
        self.assertEqual(self.history().status_code, 401)
        self.project.is_active = True
        self.project.save()
        self.assertEqual(self.history().status_code, 200)
        self.ws.is_active = False
        self.ws.save()
        self.assertEqual(self.history().status_code, 401)


class SlidingRenewalTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_use_near_expiry_extends_it(self):
        self.set_session(expires_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.history().status_code, 200)
        self.session.refresh_from_db()
        self.assertGreater(self.session.expires_at, timezone.now() + timedelta(days=25))

    def test_use_with_plenty_of_time_left_does_not_write(self):
        before = timezone.now() + timedelta(days=29)
        self.set_session(expires_at=before)
        self.assertEqual(self.history().status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.expires_at, before)

    def test_absolute_max_age_cannot_be_exceeded_by_renewal(self):
        self.set_session(expires_at=timezone.now() + timedelta(days=1))
        hard = timezone.now() + timedelta(days=1)
        VisitorSession.objects.filter(id=self.session.id).update(hard_expires_at=hard)
        self.assertEqual(self.history().status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.expires_at, hard)  # renewed, but clamped to the hard limit

    def test_session_older_than_max_age_is_invalid_even_if_expiry_is_far(self):
        VisitorSession.objects.filter(id=self.session.id).update(
            hard_expires_at=timezone.now() - timedelta(seconds=1), expires_at=timezone.now() + timedelta(days=20))
        self.assertEqual(self.history().status_code, 401)

    def test_new_session_hard_limit_comes_from_max_age_setting(self):
        s = VisitorSession.objects.create(visitor=self.visitor)
        self.assertAlmostEqual((s.hard_expires_at - timezone.now()).days, 180, delta=1)

    @override_settings(VISITOR_SESSION_TTL_DAYS=1)
    def test_ttl_is_configurable(self):
        s = VisitorSession.objects.create(visitor=self.visitor)
        self.assertLess((s.expires_at - timezone.now()), timedelta(days=1, minutes=1))


class RevokeAndRotateTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_revoke_invalidates_session_but_keeps_all_customer_data(self):
        res = self.client.post('/api/v1/widget/session/revoke/', {'session_token': self.token()}, format='json')
        self.assertEqual(res.status_code, 204)
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.revoked_at)
        for name, r in self.rest_calls(self.token()).items():
            self.assertEqual(r.status_code, 401, name)
        self.assertTrue(Visitor.objects.filter(id=self.visitor.id).exists())
        self.assertTrue(Conversation.objects.filter(id=self.conv.id).exists())
        self.assertEqual(Message.objects.filter(conversation=self.conv).count(), 1)

    def test_revoke_is_idempotent_and_reveals_nothing_about_token_validity(self):
        for tok in (self.token(), self.token(), '00000000-0000-0000-0000-000000000000', 'garbage'):
            self.assertEqual(self.client.post('/api/v1/widget/session/revoke/', {'session_token': tok}, format='json').status_code, 204)

    def test_revoke_via_header(self):
        self.assertEqual(self.client.post('/api/v1/widget/session/revoke/', HTTP_X_WIDGET_SESSION=self.token()).status_code, 204)
        self.assertEqual(self.history().status_code, 401)

    def test_revoking_one_session_does_not_affect_the_same_visitors_other_session(self):
        other = self.make_visitor_session(self.visitor)
        self.client.post('/api/v1/widget/session/revoke/', {'session_token': self.token()}, format='json')
        self.assertEqual(self.history(token=str(other.token)).status_code, 200)

    def test_rotation_swaps_token_and_kills_the_old_one(self):
        old = self.token()
        res = self.client.post('/api/v1/widget/session/rotate/', {'session_token': old}, format='json')
        self.assertEqual(res.status_code, 200)
        new = res.data['session_token']
        self.assertNotEqual(new, old)
        self.assertEqual(self.history(token=old).status_code, 401)
        self.assertEqual(self.history(token=new).status_code, 200)
        self.assertEqual(VisitorSession.objects.filter(visitor=self.visitor).count(), 1)

    def test_rotation_does_not_reset_the_absolute_max_age(self):
        hard = timezone.now() + timedelta(days=5)
        VisitorSession.objects.filter(id=self.session.id).update(hard_expires_at=hard)
        res = self.client.post('/api/v1/widget/session/rotate/', {'session_token': self.token()}, format='json')
        self.assertEqual(res.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.expires_at, hard)

    def test_rotation_requires_a_valid_session(self):
        self.set_session(expires_at=timezone.now() - timedelta(days=1))
        res = self.client.post('/api/v1/widget/session/rotate/', {'session_token': self.token()}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_start_reports_when_rotation_is_due(self):
        res = self.client.post('/api/v1/widget/start/', {'session_token': self.token()}, format='json')
        self.assertFalse(res.data['rotate_session'])
        VisitorSession.objects.filter(id=self.session.id).update(created_at=timezone.now() - timedelta(hours=25))
        res = self.client.post('/api/v1/widget/start/', {'session_token': self.token()}, format='json')
        self.assertTrue(res.data['rotate_session'])
        self.client.post('/api/v1/widget/session/rotate/', {'session_token': self.token()}, format='json')
        self.session.refresh_from_db()
        res = self.client.post('/api/v1/widget/start/', {'session_token': self.token()}, format='json')
        self.assertFalse(res.data['rotate_session'])


class LegacySessionPolicyTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_null_expiry_is_never_valid_forever(self):
        self.set_session(expires_at=None)
        self.assertEqual(self.history().status_code, 200)  # young legacy row: created_at + TTL
        VisitorSession.objects.filter(id=self.session.id).update(created_at=timezone.now() - timedelta(days=40), expires_at=None)
        self.assertEqual(self.history().status_code, 401)

    def test_effective_expiry_of_null_row(self):
        self.set_session(expires_at=None)
        self.assertAlmostEqual((effective_expiry(self.session) - self.session.created_at).days, 30, delta=1)

    def test_migration_backfill_gives_grace_and_touches_nothing_else(self):
        migration = importlib.import_module('visitors.migrations.0002_visitorsession_lifecycle')
        legacy = self.make_visitor_session(self.visitor)
        keep = self.make_visitor_session(self.visitor)
        keep_expiry = timezone.now() + timedelta(days=3)
        VisitorSession.objects.filter(id=legacy.id).update(
            expires_at=None, hard_expires_at=None, created_at=timezone.now() - timedelta(days=400))
        VisitorSession.objects.filter(id=keep.id).update(expires_at=keep_expiry)
        convs, msgs, visitors = Conversation.objects.count(), Message.objects.count(), Visitor.objects.count()
        migration.backfill_legacy_expiry(django_apps, None)
        legacy.refresh_from_db()
        keep.refresh_from_db()
        self.assertAlmostEqual((legacy.expires_at - timezone.now()).days, 30, delta=1)  # grace from deployment
        self.assertAlmostEqual((legacy.hard_expires_at - timezone.now()).days, 180, delta=1)  # lifetime also restarts
        self.assertEqual(keep.expires_at, keep_expiry)
        self.assertEqual((Conversation.objects.count(), Message.objects.count(), Visitor.objects.count()), (convs, msgs, visitors))
        # and a legacy session is renewed by simply being used during its grace period
        self.assertEqual(self.history(token=str(legacy.token)).status_code, 200)  # a 400-day-old legacy session still works
        self.assertIsNotNone(get_valid_session(str(legacy.token)))


class PurgeCommandTests(_World, TestCase):
    def setUp(self):
        self.make_world()

    def test_purge_removes_only_old_dead_sessions_and_never_customer_data(self):
        old_expired = self.make_visitor_session(self.visitor)
        old_revoked = self.make_visitor_session(self.visitor)
        recent_expired = self.make_visitor_session(self.visitor)
        VisitorSession.objects.filter(id=old_expired.id).update(expires_at=timezone.now() - timedelta(days=60))
        VisitorSession.objects.filter(id=old_revoked.id).update(revoked_at=timezone.now() - timedelta(days=60))
        VisitorSession.objects.filter(id=recent_expired.id).update(expires_at=timezone.now() - timedelta(days=1))
        out = StringIO()
        call_command('purge_expired_visitor_sessions', '--dry-run', stdout=out)
        self.assertIn('2 session(s) would be deleted', out.getvalue())
        self.assertEqual(VisitorSession.objects.count(), 4)
        call_command('purge_expired_visitor_sessions', stdout=out)
        remaining = set(VisitorSession.objects.values_list('id', flat=True))
        self.assertEqual(remaining, {self.session.id, recent_expired.id})
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Visitor.objects.filter(id=self.visitor.id).count(), 1)


class WidgetWebSocketSessionTests(_World, TransactionTestCase):
    def setUp(self):
        self.make_world()

    async def _connect(self, token, expect):
        comm = WebsocketCommunicator(application, f'/ws/widget/{token}/{self.conv.id}/')
        connected, _ = await comm.connect()
        self.assertEqual(connected, expect)
        if connected:
            await comm.disconnect()

    async def test_valid_session_connects(self):
        await self._connect(self.token(), True)

    async def test_expired_session_cannot_connect(self):
        await database_sync_to_async(self.set_session)(expires_at=timezone.now() - timedelta(seconds=1))
        await self._connect(self.token(), False)

    async def test_revoked_session_cannot_connect(self):
        await database_sync_to_async(self.set_session)(revoked_at=timezone.now())
        await self._connect(self.token(), False)

    async def test_rotated_away_token_cannot_connect(self):
        old = self.token()
        res = await database_sync_to_async(self.client.post)('/api/v1/widget/session/rotate/', {'session_token': old}, format='json')
        await self._connect(old, False)
        await self._connect(res.data['session_token'], True)
