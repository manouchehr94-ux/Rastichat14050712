from rest_framework.test import APITestCase

from . import services
from .models import KnowledgeBaseArticle
from .search import normalize_text
from .tests_base import KBTestMixin


class SearchNormalizationTests(KBTestMixin, APITestCase):
    def test_persian_yeh_kaf_normalization(self):
        # ي (Arabic Yeh, U+064A) vs ی (Persian Yeh, U+06CC); ك (Arabic Kaf,
        # U+0643) vs ک (Persian Kaf, U+06A9).
        self.assertEqual(normalize_text('ي'), 'ی')
        self.assertEqual(normalize_text('ك'), 'ک')
        self.assertEqual(normalize_text('  چگونگي  بازگشت   کالا  '), 'چگونگی بازگشت کالا')

    def test_search_persian_normalization_works(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        # Article authored with the Persian forms.
        article = services.create_article(ws, operator, title='چگونگی بازگشت کالا', body='راهنمای کامل')
        services.publish_article(article, operator)

        self.login(self.client, operator)
        # Search with the Arabic homograph forms — must still match.
        res = self.client.get('/api/v1/kb/articles/', {'q': 'چگونگي بازگشت كالا'})
        self.assertEqual(res.status_code, 200)
        ids = [a['id'] for a in res.data['results']]
        self.assertIn(str(article.id), ids)

    def test_search_english_case_insensitive(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='Refund Policy', body='How refunds work')
        services.publish_article(article, operator)

        self.login(self.client, operator)
        for query in ('refund', 'REFUND', 'ReFuNd'):
            res = self.client.get('/api/v1/kb/articles/', {'q': query})
            ids = [a['id'] for a in res.data['results']]
            self.assertIn(str(article.id), ids, f'query={query!r}')

    def test_search_ranking_is_deterministic(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        exact = services.create_article(ws, operator, title='بازگشت وجه', body='x')
        services.publish_article(exact, operator)
        contains = services.create_article(ws, operator, title='راهنمای بازگشت وجه کامل', body='x')
        services.publish_article(contains, operator)
        body_only = services.create_article(ws, operator, title='سوالات متداول', body='درباره بازگشت وجه توضیح می‌دهد')
        services.publish_article(body_only, operator)

        self.login(self.client, operator)
        res1 = self.client.get('/api/v1/kb/articles/', {'q': 'بازگشت وجه'})
        res2 = self.client.get('/api/v1/kb/articles/', {'q': 'بازگشت وجه'})
        ids1 = [a['id'] for a in res1.data['results']]
        ids2 = [a['id'] for a in res2.data['results']]
        self.assertEqual(ids1, ids2)  # deterministic across repeated identical queries
        self.assertEqual(ids1[0], str(exact.id))  # exact title match ranks first

    def test_public_search_only_returns_published_customer_visible(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        draft = services.create_article(ws, operator, title='بازگشت کالا پیش‌نویس', body='x', visibility=KnowledgeBaseArticle.Visibility.CUSTOMER)
        internal = services.create_article(ws, operator, title='بازگشت کالا داخلی', body='x', visibility=KnowledgeBaseArticle.Visibility.INTERNAL)
        services.publish_article(internal, operator)
        public = services.create_article(ws, operator, title='بازگشت کالا عمومی', body='x', visibility=KnowledgeBaseArticle.Visibility.PUBLIC)
        services.publish_article(public, operator)

        res = self.client.get(f'/api/v1/kb/public/articles/search/?project_key={project.public_key}&q=بازگشت کالا')
        titles = [a['title'] for a in res.data['results']]
        self.assertEqual(titles, ['بازگشت کالا عمومی'])
