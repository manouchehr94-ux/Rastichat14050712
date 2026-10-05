"""P1-6: private chat attachments are served only after an authorization check.

Before this change `attachment_url` was `/media/attachments/<uuid>.<ext>`: an unguessable name that nginx served to anyone, for
ever, with no relation to who asked, whether the conversation was closed, the session revoked, the member removed or the store
deactivated — and that the nginx access log recorded in full. Now `attachment_url` is a short-lived signed URL bound to the
message and to the identity it was issued to; Django re-checks that identity's CURRENT access on every fetch and answers with
`X-Accel-Redirect` so nginx streams the bytes (Range requests included).

The tests are written negative-first: every way of being refused is pinned before the happy path.
"""
import shutil
import tempfile
import time
from unittest import mock

from django.core import signing
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from conversations.models import Conversation, Message
from knowledge_base.tests_base import KBTestMixin
from platforms.models import PlatformMembership
from visitors.models import VisitorSession
from workspaces.models import WorkspaceMembership

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x85\x01\x80\x84\xa9\x8c!\x00\x00\x00\x00IEND\xaeB`\x82')
MEDIA = tempfile.mkdtemp(prefix='attach-test-media-')


def aa():
    from conversations import attachment_access
    return attachment_access


def download(token_or_url, client=None, **extra):
    client = client or APIClient()
    return client.get(token_or_url, **extra)


@override_settings(MEDIA_ROOT=MEDIA, ATTACHMENT_SERVE_MODE='accel', ATTACHMENT_URL_TTL_SECONDS=600)
class _World(KBTestMixin, TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.ws = self.make_workspace()
        self.project = self.make_project(self.ws)
        self.visitor = self.make_visitor(self.project)
        self.session = self.make_visitor_session(self.visitor)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)
        self.operator = self.make_operator(self.ws)
        self.admin = self.make_admin(self.ws, role='WORKSPACE_ADMIN')
        self.msg = self.make_attachment_message(self.conv)
        # a second store, with its own people, session and conversation
        self.ws2 = self.make_workspace()
        self.project2 = self.make_project(self.ws2)
        self.visitor2 = self.make_visitor(self.project2)
        self.session2 = self.make_visitor_session(self.visitor2)
        self.conv2 = self.make_conversation(self.ws2, self.project2, self.visitor2)
        self.operator2 = self.make_operator(self.ws2)
        self.msg2 = self.make_attachment_message(self.conv2)

    def make_attachment_message(self, conv, name='a.png', data=PNG, message_type='IMAGE'):
        msg = Message(conversation=conv, sender_type='VISITOR', content='', message_type=message_type,
                      client_message_id=f'att-{time.time_ns()}')
        msg.attachment.save(name, ContentFile(data), save=False)
        msg.save()
        return msg

    def staff_client(self, user):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        return c

    def widget_client(self, session):
        c = APIClient()
        c.credentials(HTTP_X_WIDGET_SESSION=str(session.token))
        return c

    def staff_url(self, user, msg=None):
        msg = msg or self.msg
        res = self.staff_client(user).get(f'/api/v1/attachments/{msg.id}/refresh/')
        assert res.status_code == 200, (res.status_code, getattr(res, 'data', None))
        return res.data['attachment_url']

    def visitor_url(self, session=None, msg=None):
        session = session or self.session
        msg = msg or self.msg
        res = self.widget_client(session).get(f'/api/v1/attachments/{msg.id}/refresh/')
        assert res.status_code == 200, (res.status_code, getattr(res, 'data', None))
        return res.data['attachment_url']

    def raw_token(self, msg, audience, subject=None, **kw):
        return aa().make_token(msg, audience, subject, **kw)

    def path_for(self, msg, token):
        return f'/api/v1/attachments/{msg.id}/?sig={token}'


class TokenTests(_World):
    def test_round_trip_is_bound_to_message_audience_and_subject(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        payload = aa().read_token(token)
        self.assertEqual((payload['m'], payload['a'], payload['i']), (str(self.msg.id), 'u', str(self.operator.id)))

    def test_tampering_is_rejected(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        for bad in (token[:-2] + ('AA' if not token.endswith('AA') else 'BB'), 'x' + token, token + 'x', '', 'not-a-token', token.split(':')[0]):
            self.assertIsNone(aa().read_token(bad), bad[:20])

    def test_a_token_from_another_purpose_or_key_is_rejected(self):
        other_purpose = signing.dumps({'m': str(self.msg.id), 'a': 'u', 'i': str(self.operator.id)}, salt='something-else')
        self.assertIsNone(aa().read_token(other_purpose))
        with override_settings(SECRET_KEY='a-completely-different-secret-key-for-this-check-0123456789'):
            foreign = aa().make_token(self.msg, 'u', self.operator.id)
        self.assertIsNone(aa().read_token(foreign))

    def test_expiry(self):
        with mock.patch('django.core.signing.time.time', return_value=time.time() - 601):
            old = self.raw_token(self.msg, 'u', self.operator.id)
        self.assertIsNone(aa().read_token(old))
        with mock.patch('django.core.signing.time.time', return_value=time.time() - 300):
            young = self.raw_token(self.msg, 'u', self.operator.id)
        self.assertIsNotNone(aa().read_token(young))

    def test_unknown_audience_is_refused_at_signing_time(self):
        with self.assertRaises(ValueError):
            aa().make_token(self.msg, 'admin', self.operator.id)


class NegativeDownloadTests(_World):
    """Every way of being refused answers the same uniform 404 — no oracle for which condition failed."""

    def assert_refused(self, res):
        self.assertEqual(res.status_code, 404, getattr(res, 'content', b'')[:200])
        self.assertNotIn('X-Accel-Redirect', res)
        self.assertNotIn(b'PNG', res.content)

    def test_no_signature(self):
        self.assert_refused(download(f'/api/v1/attachments/{self.msg.id}/'))

    def test_garbage_and_tampered_signature(self):
        self.assert_refused(download(self.path_for(self.msg, 'garbage')))
        token = self.raw_token(self.msg, 'u', self.operator.id)
        self.assert_refused(download(self.path_for(self.msg, token[:-3] + 'abc')))

    def test_expired_signature(self):
        with mock.patch('django.core.signing.time.time', return_value=time.time() - 601):
            token = self.raw_token(self.msg, 'u', self.operator.id)
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_signature_for_another_message_is_not_valid_here(self):
        other = self.make_attachment_message(self.conv)
        token = self.raw_token(other, 'u', self.operator.id)
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_unknown_message_and_message_without_attachment(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        self.assert_refused(download(f'/api/v1/attachments/00000000-0000-0000-0000-000000000000/?sig={token}'))
        plain = Message.objects.create(conversation=self.conv, sender_type='VISITOR', content='hi', client_message_id='plain1')
        self.assert_refused(download(self.path_for(plain, self.raw_token(plain, 'u', self.operator.id))))

    def test_operator_of_another_store_with_a_token_minted_for_them(self):
        self.assert_refused(download(self.path_for(self.msg, self.raw_token(self.msg, 'u', self.operator2.id))))

    def test_inactive_user(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        self.assertEqual(download(self.path_for(self.msg, token)).status_code, 200)
        User.objects.filter(id=self.operator.id).update(is_active=False)
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_membership_removed_closes_access_at_once(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        WorkspaceMembership.objects.filter(user=self.operator, workspace=self.ws).delete()
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_workspace_deactivated(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        type(self.ws).objects.filter(id=self.ws.id).update(is_active=False)
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_visitor_session_revoked_expired_or_project_inactive(self):
        token = self.raw_token(self.msg, 'v', self.session.id)
        self.assertEqual(download(self.path_for(self.msg, token)).status_code, 200)
        VisitorSession.objects.filter(id=self.session.id).update(revoked_at=timezone.now())
        self.assert_refused(download(self.path_for(self.msg, token)))
        VisitorSession.objects.filter(id=self.session.id).update(revoked_at=None, expires_at=timezone.now() - timezone.timedelta(minutes=1))
        self.assert_refused(download(self.path_for(self.msg, token)))
        VisitorSession.objects.filter(id=self.session.id).update(expires_at=timezone.now() + timezone.timedelta(days=1))
        self.assertEqual(download(self.path_for(self.msg, token)).status_code, 200)
        type(self.project).objects.filter(id=self.project.id).update(is_active=False)
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_another_visitors_session_is_refused_even_with_a_crafted_token(self):
        self.assert_refused(download(self.path_for(self.msg, self.raw_token(self.msg, 'v', self.session2.id))))
        self.assert_refused(download(self.path_for(self.msg2, self.raw_token(self.msg2, 'v', self.session.id))))

    def test_conversation_visitor_audience_dies_with_the_visitors_sessions(self):
        token = self.raw_token(self.msg, 'c', None)
        self.assertEqual(download(self.path_for(self.msg, token)).status_code, 200)
        VisitorSession.objects.filter(visitor=self.visitor).update(revoked_at=timezone.now())
        self.assert_refused(download(self.path_for(self.msg, token)))

    def test_staff_token_cannot_be_used_as_a_visitor_token_or_vice_versa(self):
        # same numeric/uuid subject, wrong audience: the audience decides which identity table is consulted
        self.assert_refused(download(self.path_for(self.msg, self.raw_token(self.msg, 'v', self.operator.id))))
        self.assert_refused(download(self.path_for(self.msg, self.raw_token(self.msg, 'u', self.session.id))))

    def test_support_conversation_attachments_need_workspace_admin_or_platform_staff(self):
        support = Conversation.objects.create(workspace=self.ws, type=Conversation.Type.PLATFORM_SUPPORT,
                                              status=Conversation.Status.WAITING_FOR_PLATFORM, subject='s')
        msg = self.make_attachment_message(support)
        self.assertEqual(download(self.path_for(msg, self.raw_token(msg, 'u', self.admin.id))).status_code, 200)
        self.assert_refused(download(self.path_for(msg, self.raw_token(msg, 'u', self.operator.id))))  # operator is not support staff
        agent = self.make_user()
        PlatformMembership.objects.create(user=agent, platform=self.ws.platform, role='PLATFORM_SUPPORT_AGENT')
        self.assertEqual(download(self.path_for(msg, self.raw_token(msg, 'u', agent.id))).status_code, 200)
        self.assert_refused(download(self.path_for(msg, self.raw_token(msg, 'u', self.operator2.id))))
        # a visitor can never reach a support conversation
        self.assert_refused(download(self.path_for(msg, self.raw_token(msg, 'v', self.session.id))))

    def test_only_get_and_head_are_allowed(self):
        token = self.raw_token(self.msg, 'u', self.operator.id)
        for method in ('post', 'put', 'patch', 'delete'):
            res = getattr(APIClient(), method)(self.path_for(self.msg, token))
            self.assertEqual(res.status_code, 405, method)


class PositiveDownloadTests(_World):
    def test_authorized_staff_gets_an_accel_redirect_with_private_headers(self):
        res = download(self.path_for(self.msg, self.raw_token(self.msg, 'u', self.operator.id)))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['X-Accel-Redirect'], '/protected-media/' + self.msg.attachment.name)
        self.assertEqual(res.content, b'')  # Django authorizes, nginx streams
        self.assertEqual(res['Content-Type'], 'image/png')
        self.assertEqual(res['Cache-Control'], 'private, no-store')
        self.assertEqual(res['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(res['Referrer-Policy'], 'no-referrer')
        self.assertTrue(res['Content-Disposition'].startswith('inline'))

    def test_voice_notes_get_an_audio_type(self):
        for name, mime in (('v.webm', 'audio/webm'), ('v.ogg', 'audio/ogg'), ('v.mp3', 'audio/mpeg'), ('v.m4a', 'audio/mp4'), ('v.wav', 'audio/wav')):
            msg = self.make_attachment_message(self.conv, name=name, data=b'RIFFxxxx', message_type='VOICE')
            res = download(self.path_for(msg, self.raw_token(msg, 'u', self.operator.id)))
            self.assertEqual((res.status_code, res['Content-Type']), (200, mime), name)

    def test_the_customers_own_session_and_the_conversation_audience_work(self):
        self.assertEqual(download(self.path_for(self.msg, self.raw_token(self.msg, 'v', self.session.id))).status_code, 200)
        self.assertEqual(download(self.path_for(self.msg, self.raw_token(self.msg, 'c', None))).status_code, 200)

    def test_range_header_is_passed_through_untouched_for_nginx_to_honour(self):
        res = download(self.path_for(self.msg, self.raw_token(self.msg, 'u', self.operator.id)), HTTP_RANGE='bytes=0-9')
        self.assertEqual(res.status_code, 200)  # nginx turns the redirected file into a 206; Django must not interfere
        self.assertIn('X-Accel-Redirect', res)

    @override_settings(ATTACHMENT_SERVE_MODE='django')
    def test_django_mode_streams_the_file_for_development(self):
        res = download(self.path_for(self.msg, self.raw_token(self.msg, 'u', self.operator.id)))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(b''.join(res.streaming_content), PNG)
        self.assertNotIn('X-Accel-Redirect', res)
        self.assertEqual(res['Cache-Control'], 'private, no-store')


class SerializerAudienceTests(_World):
    def history(self, client, path):
        res = client.get(path)
        self.assertEqual(res.status_code, 200)
        return [m for m in res.data if m['id'] == str(self.msg.id)][0]

    def test_staff_history_urls_are_signed_for_that_user_and_never_expose_the_media_path(self):
        row = self.history(self.staff_client(self.operator), f'/api/v1/conversations/{self.conv.id}/messages/')
        url = row['attachment_url']
        self.assertIn('/api/v1/attachments/', url)
        self.assertIn('sig=', url)
        self.assertNotIn('/media/', url)
        self.assertNotIn(self.msg.attachment.name, url)
        sig = url.split('sig=')[1]
        self.assertEqual(aa().read_token(sig)['i'], str(self.operator.id))
        self.assertEqual(download(url).status_code, 200)

    def test_visitor_history_urls_are_bound_to_the_visitors_session(self):
        row = self.history(self.widget_client(self.session), f'/api/v1/widget/conversations/{self.conv.id}/messages/')
        payload = aa().read_token(row['attachment_url'].split('sig=')[1])
        self.assertEqual((payload['a'], payload['i']), ('v', str(self.session.id)))
        self.assertEqual(download(row['attachment_url']).status_code, 200)

    def test_two_people_never_share_a_url(self):
        a = self.staff_url(self.operator)
        b = self.staff_url(self.admin)
        self.assertNotEqual(a, b)

    def test_no_request_context_means_no_url(self):
        from conversations.serializers import MessageSerializer
        self.assertIsNone(MessageSerializer(self.msg).data['attachment_url'])

    def test_messages_without_attachment_have_no_url(self):
        from conversations.serializers import MessageSerializer
        plain = Message.objects.create(conversation=self.conv, sender_type='VISITOR', content='x', client_message_id='plain2')
        self.assertIsNone(MessageSerializer(plain, context={'attachment_audience': ('c', None)}).data['attachment_url'])

    def test_explicit_conversation_audience_for_broadcasts(self):
        from conversations.serializers import MessageSerializer
        url = MessageSerializer(self.msg, context={'attachment_audience': ('c', None)}).data['attachment_url']
        self.assertEqual(aa().read_token(url.split('sig=')[1])['a'], 'c')

    def test_uploads_broadcast_a_conversation_audience_url_not_the_uploaders_identity(self):
        captured = []
        with mock.patch('conversations.views._broadcast', side_effect=lambda cid, data, *a, **k: captured.append(data)):
            res = self.widget_client(self.session).post(
                f'/api/v1/widget/conversations/{self.conv.id}/upload/',
                {'file': ContentFile(PNG, name='u.png'), 'message_type': 'IMAGE', 'client_message_id': 'up-1'}, format='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        own = aa().read_token(res.data['attachment_url'].split('sig=')[1])
        self.assertEqual(own['a'], 'v')  # the uploader's own response is bound to the uploader
        broadcast = aa().read_token(captured[0]['attachment_url'].split('sig=')[1])
        self.assertEqual(broadcast['a'], 'c')  # what every socket in the room receives is not


class RefreshEndpointTests(_World):
    """One endpoint for both kinds of client: GET /attachments/<message>/refresh/ with the staff JWT or the visitor session header."""

    def path(self, msg=None):
        return f'/api/v1/attachments/{(msg or self.msg).id}/refresh/'

    def test_staff_member_gets_a_fresh_url_bound_to_them(self):
        url = self.staff_url(self.operator)
        self.assertEqual(aa().read_token(url.split('sig=')[1])['a'], 'u')
        self.assertEqual(download(url).status_code, 200)

    def test_staff_refusals(self):
        self.assertEqual(APIClient().get(self.path()).status_code, 401)  # nobody
        self.assertEqual(self.staff_client(self.operator2).get(self.path()).status_code, 404)  # another store
        self.assertEqual(self.staff_client(self.operator).get(self.path(self.msg2)).status_code, 404)  # a message of another store
        self.assertEqual(self.staff_client(self.operator).get('/api/v1/attachments/00000000-0000-0000-0000-000000000000/refresh/').status_code, 404)
        plain = Message.objects.create(conversation=self.conv, sender_type='VISITOR', content='hi', client_message_id='plain3')
        self.assertEqual(self.staff_client(self.operator).get(self.path(plain)).status_code, 404)
        WorkspaceMembership.objects.filter(user=self.operator, workspace=self.ws).delete()
        self.assertEqual(self.staff_client(self.operator).get(self.path()).status_code, 404)  # membership removed
        User.objects.filter(id=self.admin.id).update(is_active=False)
        self.assertIn(self.staff_client(self.admin).get(self.path()).status_code, (401, 403, 404))  # inactive user

    def test_visitor_gets_a_fresh_url_bound_to_their_session(self):
        url = self.visitor_url()
        self.assertEqual(aa().read_token(url.split('sig=')[1])['a'], 'v')
        self.assertEqual(download(url).status_code, 200)

    def test_visitor_refusals(self):
        self.assertEqual(self.widget_client(self.session2).get(self.path()).status_code, 404)  # another visitor, valid session
        VisitorSession.objects.filter(id=self.session.id).update(revoked_at=timezone.now())
        res = self.widget_client(self.session).get(self.path())
        self.assertEqual((res.status_code, res.data.get('code')), (401, 'session_invalid'))
        VisitorSession.objects.filter(id=self.session.id).update(revoked_at=None)
        type(self.project).objects.filter(id=self.project.id).update(is_active=False)
        self.assertEqual(self.widget_client(self.session).get(self.path()).status_code, 401)  # project inactive: the session is no longer valid

    def test_the_widget_alias_serves_the_same_view_under_the_cors_governed_prefix(self):
        res = self.widget_client(self.session).get(f'/api/v1/widget/attachments/{self.msg.id}/refresh/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(download(res.data['attachment_url']).status_code, 200)

    @override_settings(LEGACY_URL_CREDENTIALS_ENABLED=False)  # the staging/production default (on only for local development)
    def test_the_session_credential_is_never_accepted_in_the_query_string(self):
        self.assertEqual(APIClient().get(self.path() + f'?session_token={self.session.token}').status_code, 401)
