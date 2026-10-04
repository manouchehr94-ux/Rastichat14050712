import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from platforms.models import Platform
from workspaces.models import Workspace
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation, Message


class WidgetInitVisitorIdentityTests(TestCase):
    """Regression coverage for a real bug found via E2E testing: anonymous
    visitor init previously used `Visitor.objects.get_or_create(project=project)`,
    which matches on `project` alone — every anonymous session for the same
    project collapsed onto the *same* Visitor (and therefore the same
    Conversation), so unrelated customers could end up sharing one chat
    history. Anonymous sessions must each get their own Visitor; only a
    caller-supplied `external_id` should make two sessions resolve to the
    same Visitor (a genuinely known/returning customer).
    """

    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)

    def _init(self, **extra):
        return self.client.post(
            '/api/v1/widget/init/', {'project_key': str(self.project.public_key), **extra}, format='json',
        )

    def test_two_anonymous_sessions_get_distinct_visitors(self):
        res1 = self._init()
        res2 = self._init()
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)
        self.assertNotEqual(res1.data['visitor_id'], res2.data['visitor_id'])
        self.assertEqual(Visitor.objects.filter(project=self.project).count(), 2)

    def test_same_unsigned_external_id_does_not_resolve_to_same_visitor(self):
        res1 = self._init(external_id='crm-123')
        res2 = self._init(external_id='crm-123')
        self.assertNotEqual(res1.data['visitor_id'], res2.data['visitor_id'])
        self.assertEqual(Visitor.objects.filter(project=self.project).count(), 2)

    def test_unsigned_external_id_is_only_an_unverified_hint(self):
        res = self._init(external_id='crm-123', name='Someone')
        visitor = Visitor.objects.get(id=res.data['visitor_id'])
        self.assertIsNone(visitor.external_id)
        self.assertEqual(visitor.metadata, {'unverified_external_id': 'crm-123'})

    def test_different_external_ids_get_distinct_visitors(self):
        res1 = self._init(external_id='crm-123')
        res2 = self._init(external_id='crm-456')
        self.assertNotEqual(res1.data['visitor_id'], res2.data['visitor_id'])
        self.assertEqual(Visitor.objects.filter(project=self.project).count(), 2)

    def test_anonymous_and_identified_sessions_do_not_collide(self):
        anon = self._init()
        identified = self._init(external_id='crm-123')
        self.assertNotEqual(anon.data['visitor_id'], identified.data['visitor_id'])


class ExternalIdSpoofingTests(TestCase):
    """P0-4: a public caller who knows/guesses a customer's external_id must
    not be able to resume that customer's conversation or read its history."""

    def setUp(self):
        self.client = APIClient()
        platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)

    def _init(self, **extra):
        return self.client.post(
            '/api/v1/widget/init/', {'project_key': str(self.project.public_key), **extra}, format='json',
        )

    def _start(self, token):
        return self.client.post('/api/v1/widget/start/', {'session_token': token}, format='json')

    def _history(self, conv_id, token):
        return self.client.get(f'/api/v1/widget/conversations/{conv_id}/messages/', {'session_token': token})

    def test_attacker_with_victim_external_id_cannot_read_history(self):
        victim = self._init(external_id='cust-42', name='Victim')
        conv_id = self._start(victim.data['session_token']).data['id']
        Message.objects.create(
            conversation_id=conv_id, sender_type='VISITOR', sender_visitor_id=victim.data['visitor_id'],
            content='my private order question', client_message_id='m1',
        )
        attacker = self._init(external_id='cust-42', name='Attacker')
        attacker_conv = self._start(attacker.data['session_token']).data['id']
        self.assertNotEqual(attacker_conv, conv_id)
        self.assertEqual(self._history(attacker_conv, attacker.data['session_token']).data, [])
        # and cannot address the victim's conversation with their own token
        self.assertEqual(self._history(conv_id, attacker.data['session_token']).status_code, 404)

    def test_returning_customer_resumes_with_own_session_token(self):
        first = self._init(external_id='cust-42')
        conv_id = self._start(first.data['session_token']).data['id']
        Message.objects.create(
            conversation_id=conv_id, sender_type='VISITOR', sender_visitor_id=first.data['visitor_id'],
            content='hello', client_message_id='m1',
        )
        # the widget keeps session_token in localStorage and calls /start/ again
        again = self._start(first.data['session_token'])
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data['id'], conv_id)
        self.assertEqual([m['content'] for m in self._history(conv_id, first.data['session_token']).data], ['hello'])

    def test_preexisting_identified_visitor_session_still_works(self):
        legacy = Visitor.objects.create(project=self.project, external_id='legacy-1', name='Old')
        session = VisitorSession.objects.create(visitor=legacy)
        res = self._start(str(session.token))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Conversation.objects.get(id=res.data['id']).visitor_id, legacy.id)

    def test_new_init_never_attaches_to_preexisting_identified_visitor(self):
        legacy = Visitor.objects.create(project=self.project, external_id='legacy-1', name='Old')
        res = self._init(external_id='legacy-1')
        self.assertNotEqual(res.data['visitor_id'], str(legacy.id))

    @override_settings(WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID=True)
    def test_legacy_flag_restores_old_lookup_and_warns(self):
        with self.assertLogs('visitors.views', level='WARNING') as logs:
            res1 = self._init(external_id='crm-123')
            res2 = self._init(external_id='crm-123')
        self.assertEqual(res1.data['visitor_id'], res2.data['visitor_id'])
        self.assertEqual(len(logs.records), 2)

    def test_default_setting_is_secure(self):
        from django.conf import settings
        self.assertFalse(settings.WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID)

    def test_unknown_project_key_rejected(self):
        res = self.client.post('/api/v1/widget/init/', {'project_key': str(uuid.uuid4()), 'external_id': 'x'}, format='json')
        self.assertEqual(res.status_code, 400)
