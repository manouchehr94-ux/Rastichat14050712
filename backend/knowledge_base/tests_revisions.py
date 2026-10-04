from rest_framework.test import APITestCase

from . import services
from .models import KnowledgeBaseArticle
from .tests_base import KBTestMixin


class RevisionTests(KBTestMixin, APITestCase):
    def test_revision_created_on_edit(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='عنوان اول', body='متن اول')
        self.assertEqual(article.revisions.count(), 1)

        article = services.update_article_content(article, operator, body='متن دوم', change_summary='به‌روزرسانی')
        self.assertEqual(article.revisions.count(), 2)
        self.assertEqual(article.current_revision_number, 2)
        latest = article.revisions.order_by('-revision_number').first()
        self.assertEqual(latest.body, 'متن دوم')
        self.assertEqual(latest.change_summary, 'به‌روزرسانی')

    def test_metadata_only_update_does_not_create_revision(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='عنوان', body='متن')
        self.assertEqual(article.revisions.count(), 1)
        services.update_article_content(article, operator, is_featured=True)
        self.assertEqual(article.revisions.count(), 1)

    def test_restore_creates_a_new_revision(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='نسخه ۱', body='متن ۱')
        services.update_article_content(article, operator, title='نسخه ۲', body='متن ۲')
        services.update_article_content(article, operator, title='نسخه ۳', body='متن ۳')
        self.assertEqual(article.revisions.count(), 3)

        rev1 = article.revisions.get(revision_number=1)
        article = services.restore_revision(article, operator, rev1)

        self.assertEqual(article.revisions.count(), 4)  # nothing deleted, a new one appended
        self.assertEqual(article.title, 'نسخه ۱')
        self.assertEqual(article.current_revision_number, 4)
        # The full timeline (including the ones "restored past") is intact.
        self.assertEqual(article.revisions.get(revision_number=2).title, 'نسخه ۲')
        self.assertEqual(article.revisions.get(revision_number=3).title, 'نسخه ۳')

    def test_publish_with_specific_revision_restores_then_publishes(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='نسخه ۱', body='متن ۱')
        services.update_article_content(article, operator, title='نسخه ۲', body='متن ۲')
        rev1 = article.revisions.get(revision_number=1)

        article = services.publish_article(article, operator, revision=rev1)
        self.assertEqual(article.status, KnowledgeBaseArticle.Status.PUBLISHED)
        self.assertEqual(article.title, 'نسخه ۱')

    def test_restore_revision_api(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        self.login(self.client, operator)
        article = services.create_article(ws, operator, title='اول', body='متن اول')
        services.update_article_content(article, operator, title='دوم', body='متن دوم')

        res = self.client.get(f'/api/v1/kb/articles/{article.id}/revisions/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 2)

        res = self.client.post(f'/api/v1/kb/articles/{article.id}/revisions/1/restore/')
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.data['title'], 'اول')
