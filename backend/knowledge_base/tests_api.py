from rest_framework.test import APITestCase

from .models import KnowledgeBaseArticle, KnowledgeBaseCategory
from .tests_base import KBTestMixin


class CategoryPermissionTests(KBTestMixin, APITestCase):
    def test_admin_creates_category(self):
        ws = self.make_workspace()
        admin = self.make_admin(ws)
        self.login(self.client, admin)
        res = self.client.post('/api/v1/kb/categories/', {'workspace': ws.id, 'name': 'سفارش‌ها', 'slug': 'orders'}, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(KnowledgeBaseCategory.objects.filter(workspace=ws).count(), 1)

    def test_operator_cannot_manage_categories(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        self.login(self.client, operator)
        res = self.client.post('/api/v1/kb/categories/', {'workspace': ws.id, 'name': 'سفارش‌ها', 'slug': 'orders'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_operator_can_view_categories(self):
        ws = self.make_workspace()
        admin = self.make_admin(ws)
        cat = KnowledgeBaseCategory.objects.create(workspace=ws, name='پرداخت', slug='payment')
        operator = self.make_operator(ws)
        self.login(self.client, operator)
        res = self.client.get('/api/v1/kb/categories/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['results']), 1)

    def test_nested_categories_work(self):
        ws = self.make_workspace()
        admin = self.make_admin(ws)
        parent = KnowledgeBaseCategory.objects.create(workspace=ws, name='سفارش‌ها', slug='orders')
        self.login(self.client, admin)
        res = self.client.post(
            '/api/v1/kb/categories/',
            {'workspace': ws.id, 'name': 'پیگیری سفارش', 'slug': 'order-tracking', 'parent': str(parent.id)},
            format='json',
        )
        self.assertEqual(res.status_code, 201, res.content)
        child = KnowledgeBaseCategory.objects.get(id=res.data['id'])
        self.assertEqual(child.parent_id, parent.id)
        self.assertIn(child, parent.children.all())

    def test_category_cannot_be_its_own_ancestor(self):
        ws = self.make_workspace()
        admin = self.make_admin(ws)
        cat = KnowledgeBaseCategory.objects.create(workspace=ws, name='A', slug='a')
        self.login(self.client, admin)
        res = self.client.patch(f'/api/v1/kb/categories/{cat.id}/', {'parent': str(cat.id)}, format='json')
        self.assertEqual(res.status_code, 400)


class ArticleVisibilityTests(KBTestMixin, APITestCase):
    def test_draft_article_is_private_from_public_api(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        self.login(self.client, operator)
        res = self.client.post('/api/v1/kb/articles/', {
            'workspace': ws.id, 'title': 'راهنمای بازگشت وجه', 'body': 'متن راهنما',
            'visibility': KnowledgeBaseArticle.Visibility.CUSTOMER,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        article = KnowledgeBaseArticle.objects.get(id=res.data['id'])
        self.assertEqual(article.status, KnowledgeBaseArticle.Status.DRAFT)

        pub = self.client.get(f'/api/v1/kb/public/articles/{article.slug}/?project_key={project.public_key}')
        self.assertEqual(pub.status_code, 404)

    def test_internal_article_never_reaches_visitor(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        from . import services
        article = services.create_article(
            ws, operator, title='یادداشت داخلی', body='فقط برای اپراتورها',
            visibility=KnowledgeBaseArticle.Visibility.INTERNAL,
        )
        services.publish_article(article, operator)

        res = self.client.get(f'/api/v1/kb/public/articles/{article.slug}/?project_key={project.public_key}')
        self.assertEqual(res.status_code, 404)
        res = self.client.get(f'/api/v1/kb/public/articles/?project_key={project.public_key}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['count'], 0)

    def test_published_customer_article_is_visible(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        from . import services
        article = services.create_article(
            ws, operator, title='چگونه سفارش را پیگیری کنیم', body='متن',
            visibility=KnowledgeBaseArticle.Visibility.CUSTOMER,
        )
        services.publish_article(article, operator)

        res = self.client.get(f'/api/v1/kb/public/articles/{article.slug}/?project_key={project.public_key}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['title'], 'چگونه سفارش را پیگیری کنیم')

    def test_cross_workspace_uuid_rejected(self):
        ws1 = self.make_workspace()
        ws2 = self.make_workspace()
        project2 = self.make_project(ws2)
        operator1 = self.make_operator(ws1)
        from . import services
        article = services.create_article(
            ws1, operator1, title='مقاله فضای‌کار ۱', body='متن',
            visibility=KnowledgeBaseArticle.Visibility.PUBLIC,
        )
        services.publish_article(article, operator1)

        # Same slug, but requested against the OTHER workspace's project_key.
        res = self.client.get(f'/api/v1/kb/public/articles/{article.slug}/?project_key={project2.public_key}')
        self.assertEqual(res.status_code, 404)

        operator2 = self.make_operator(ws2)
        self.login(self.client, operator2)
        res = self.client.get(f'/api/v1/kb/articles/{article.id}/')
        self.assertEqual(res.status_code, 404)

    def test_archive_hides_article_from_public(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        from . import services
        article = services.create_article(
            ws, operator, title='مقاله قدیمی', body='متن', visibility=KnowledgeBaseArticle.Visibility.PUBLIC,
        )
        services.publish_article(article, operator)
        res = self.client.get(f'/api/v1/kb/public/articles/{article.slug}/?project_key={project.public_key}')
        self.assertEqual(res.status_code, 200)

        services.archive_article(article, operator)
        res = self.client.get(f'/api/v1/kb/public/articles/{article.slug}/?project_key={project.public_key}')
        self.assertEqual(res.status_code, 404)

    def test_publish_records_actor_and_time(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        from . import services
        article = services.create_article(ws, operator, title='مقاله', body='متن')
        self.assertIsNone(article.published_at)
        article = services.publish_article(article, operator)
        self.assertEqual(article.published_by_id, operator.id)
        self.assertIsNotNone(article.published_at)

    def test_visibility_filtering_excludes_internal_from_customer_list(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        from . import services
        internal = services.create_article(ws, operator, title='داخلی', body='x', visibility=KnowledgeBaseArticle.Visibility.INTERNAL)
        services.publish_article(internal, operator)
        customer = services.create_article(ws, operator, title='مشتری', body='x', visibility=KnowledgeBaseArticle.Visibility.CUSTOMER)
        services.publish_article(customer, operator)

        res = self.client.get(f'/api/v1/kb/public/articles/?project_key={project.public_key}')
        titles = [a['title'] for a in res.data['results']]
        self.assertIn('مشتری', titles)
        self.assertNotIn('داخلی', titles)
