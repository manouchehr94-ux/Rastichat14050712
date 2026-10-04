from rest_framework.test import APITestCase

from . import services
from .models import KnowledgeBaseArticle, KnowledgeBaseArticleFeedback
from .tests_base import KBTestMixin


class FeedbackTests(KBTestMixin, APITestCase):
    def _published_article(self, ws, operator):
        article = services.create_article(ws, operator, title='مقاله', body='متن', visibility=KnowledgeBaseArticle.Visibility.PUBLIC)
        return services.publish_article(article, operator)

    def test_feedback_accepted_once(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        article = self._published_article(ws, operator)
        visitor = self.make_visitor(project)
        session = self.make_visitor_session(visitor)

        res = self.client.post(
            f'/api/v1/kb/public/articles/{article.slug}/feedback/',
            {'session_token': str(session.token), 'is_helpful': True, 'project_key': str(project.public_key)},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(KnowledgeBaseArticleFeedback.objects.filter(article=article).count(), 1)
        self.assertTrue(KnowledgeBaseArticleFeedback.objects.get(article=article).is_helpful)

    def test_duplicate_feedback_is_deterministic_upsert(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        article = self._published_article(ws, operator)
        visitor = self.make_visitor(project)
        session = self.make_visitor_session(visitor)

        self.client.post(
            f'/api/v1/kb/public/articles/{article.slug}/feedback/',
            {'session_token': str(session.token), 'is_helpful': True, 'project_key': str(project.public_key)},
            format='json',
        )
        res = self.client.post(
            f'/api/v1/kb/public/articles/{article.slug}/feedback/',
            {'session_token': str(session.token), 'is_helpful': False, 'comment': 'در واقع مفید نبود', 'project_key': str(project.public_key)},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        # Still exactly one row (no duplicate), and the SECOND submission won
        # deterministically.
        self.assertEqual(KnowledgeBaseArticleFeedback.objects.filter(article=article).count(), 1)
        feedback = KnowledgeBaseArticleFeedback.objects.get(article=article)
        self.assertFalse(feedback.is_helpful)
        self.assertEqual(feedback.comment, 'در واقع مفید نبود')

    def test_feedback_summary_endpoint(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        article = self._published_article(ws, operator)
        for i in range(3):
            visitor = self.make_visitor(project, external_id=f'v{i}')
            session = self.make_visitor_session(visitor)
            self.client.post(
                f'/api/v1/kb/public/articles/{article.slug}/feedback/',
                {'session_token': str(session.token), 'is_helpful': i != 0, 'project_key': str(project.public_key)},
                format='json',
            )
        self.login(self.client, operator)
        res = self.client.get(f'/api/v1/kb/articles/{article.id}/feedback-summary/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {'helpful': 2, 'not_helpful': 1, 'total': 3})

    def test_feedback_workspace_isolation(self):
        ws1 = self.make_workspace()
        project1 = self.make_project(ws1)
        operator1 = self.make_operator(ws1)
        article1 = self._published_article(ws1, operator1)

        ws2 = self.make_workspace()
        operator2 = self.make_operator(ws2)
        self.login(self.client, operator2)
        res = self.client.get(f'/api/v1/kb/articles/{article1.id}/feedback-summary/')
        self.assertEqual(res.status_code, 403)
