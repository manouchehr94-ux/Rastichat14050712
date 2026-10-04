from django.test import TestCase
from rest_framework.test import APIClient
from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from catalog.models import Product


class CatalogTenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.other_platform = Platform.objects.create(name='P2')
        self.other_workspace = Workspace.objects.create(name='WS2', platform=self.other_platform)

        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')

        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.token = res.data['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def test_list_only_returns_own_workspace_products(self):
        mine = Product.objects.create(workspace=self.workspace, name='Mine', brand='Arom', price=1000)
        Product.objects.create(workspace=self.other_workspace, name='NotMine', brand='X', price=1000)
        res = self.client.get('/api/v1/products/')
        self.assertEqual(res.status_code, 200)
        ids = [p['id'] for p in res.data]
        self.assertIn(str(mine.id), ids)
        self.assertEqual(len(ids), 1)

    def test_list_excludes_inactive_products(self):
        Product.objects.create(workspace=self.workspace, name='Hidden', brand='Arom', price=1000, is_active=False)
        res = self.client.get('/api/v1/products/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 0)

    def test_single_workspace_operator_create_defaults_to_own_workspace(self):
        res = self.client.post('/api/v1/products/', {
            'name': 'Candle', 'brand': 'Arom', 'price': 890000,
        }, format='json')
        self.assertEqual(res.status_code, 201)
        product = Product.objects.get(id=res.data['id'])
        self.assertEqual(product.workspace_id, self.workspace.id)

    def test_multi_workspace_operator_must_specify_workspace(self):
        second_ws = Workspace.objects.create(name='WS3', platform=self.platform)
        WorkspaceMembership.objects.create(user=self.operator, workspace=second_ws, role='WORKSPACE_OPERATOR')
        res = self.client.post('/api/v1/products/', {'name': 'Ambiguous', 'brand': 'Arom', 'price': 1000}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_multi_workspace_operator_can_target_explicit_workspace(self):
        second_ws = Workspace.objects.create(name='WS3', platform=self.platform)
        WorkspaceMembership.objects.create(user=self.operator, workspace=second_ws, role='WORKSPACE_OPERATOR')
        res = self.client.post('/api/v1/products/', {
            'name': 'Explicit', 'brand': 'Arom', 'price': 1000, 'workspace_id': str(second_ws.id),
        }, format='json')
        self.assertEqual(res.status_code, 201)
        product = Product.objects.get(id=res.data['id'])
        self.assertEqual(product.workspace_id, second_ws.id)

    def test_cannot_create_product_in_foreign_workspace(self):
        res = self.client.post('/api/v1/products/', {
            'name': 'Sneaky', 'brand': 'Arom', 'price': 1000, 'workspace_id': str(self.other_workspace.id),
        }, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Product.objects.filter(name='Sneaky').exists())

    def test_operator_cannot_see_or_edit_foreign_product(self):
        foreign = Product.objects.create(workspace=self.other_workspace, name='Foreign', brand='X', price=1000)
        res = self.client.get(f'/api/v1/products/{foreign.id}/')
        self.assertEqual(res.status_code, 404)
        res = self.client.patch(f'/api/v1/products/{foreign.id}/', {'name': 'Hacked'}, format='json')
        self.assertEqual(res.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.name, 'Foreign')
