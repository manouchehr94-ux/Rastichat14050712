from django.test import TestCase
from rest_framework.test import APIClient

from platforms.models import Platform
from workspaces.models import Workspace
from projects.models import Project
from visitors.models import Visitor


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

    def test_same_external_id_resolves_to_same_visitor(self):
        res1 = self._init(external_id='crm-123')
        res2 = self._init(external_id='crm-123')
        self.assertEqual(res1.data['visitor_id'], res2.data['visitor_id'])
        self.assertEqual(Visitor.objects.filter(project=self.project).count(), 1)

    def test_different_external_ids_get_distinct_visitors(self):
        res1 = self._init(external_id='crm-123')
        res2 = self._init(external_id='crm-456')
        self.assertNotEqual(res1.data['visitor_id'], res2.data['visitor_id'])
        self.assertEqual(Visitor.objects.filter(project=self.project).count(), 2)

    def test_anonymous_and_identified_sessions_do_not_collide(self):
        anon = self._init()
        identified = self._init(external_id='crm-123')
        self.assertNotEqual(anon.data['visitor_id'], identified.data['visitor_id'])
