from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from accounts.presence import touch_presence
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation
from conversations.branding import build_widget_branding
from catalog.models import Product


class WidgetBrandingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(
            name='Real Store Name', workspace=self.workspace,
            logo_url='https://example.com/logo.png', subtitle='خانه و سبک زندگی',
        )
        self.operator = User.objects.create_user(
            email='op@test.com', password='pass1234', display_name='مریم رضایی', title='مشاور ارشد',
        )
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project, name='Sara')
        self.session = VisitorSession.objects.create(visitor=self.visitor)

    def test_start_chat_returns_real_store_branding_never_hardcoded(self):
        res = self.client.post('/api/v1/widget/start/', {'session_token': str(self.session.token)}, format='json')
        self.assertEqual(res.status_code, 200)
        branding = res.data['branding']
        self.assertEqual(branding['store']['name'], 'Real Store Name')
        self.assertEqual(branding['store']['logo_url'], 'https://example.com/logo.png')
        self.assertEqual(branding['store']['subtitle'], 'خانه و سبک زندگی')
        # No consultant is shown until a real conversation assignment exists.
        self.assertIsNone(branding['consultant'])

    def test_consultant_identity_only_shown_after_real_assignment(self):
        conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, assigned_to=self.operator,
        )
        branding = build_widget_branding(conv)
        self.assertEqual(branding['consultant']['display_name'], 'مریم رضایی')
        self.assertEqual(branding['consultant']['title'], 'مشاور ارشد')
        self.assertIn(branding['consultant']['status'], ('ONLINE', 'AWAY', 'OFFLINE'))

    def test_consultant_display_name_falls_back_to_email_prefix_never_a_sample_name(self):
        nameless = User.objects.create_user(email='nameless@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=nameless, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, assigned_to=nameless,
        )
        branding = build_widget_branding(conv)
        self.assertEqual(branding['consultant']['display_name'], 'nameless')
        self.assertNotIn('مریم', branding['consultant']['display_name'])

    def test_workspace_online_reflects_real_presence(self):
        conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )
        self.assertFalse(build_widget_branding(conv)['workspace_online'])
        touch_presence(self.operator)  # operator becomes active -> ONLINE
        self.assertTrue(build_widget_branding(conv)['workspace_online'])

    def test_branding_endpoint_is_workspace_scoped(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        other_project = Project.objects.create(name='Other Store', workspace=other_ws)
        other_visitor = Visitor.objects.create(project=other_project)
        other_session = VisitorSession.objects.create(visitor=other_visitor)
        other_conv = Conversation.objects.create(
            visitor=other_visitor, workspace=other_ws, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )
        # Visitor A's session must not be able to read workspace B's branding via conv B's id.
        res = self.client.get(
            f'/api/v1/widget/conversations/{other_conv.id}/branding/?session_token={self.session.token}'
        )
        self.assertEqual(res.status_code, 404)


class AssignmentAndStatusTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.op1 = User.objects.create_user(email='op1@test.com', password='pass1234', display_name='Op One')
        self.op2 = User.objects.create_user(email='op2@test.com', password='pass1234', display_name='Op Two')
        WorkspaceMembership.objects.create(user=self.op1, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        WorkspaceMembership.objects.create(user=self.op2, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'op1@test.com', 'password': 'pass1234'}, format='json')
        self.token1 = res.data['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token1}')

    def test_self_assign(self):
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/assign/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['assigned_to']['id'], str(self.op1.id))

    def test_reassign_to_teammate_in_same_workspace(self):
        self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/assign/', format='json')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/assign/', {'operator_id': str(self.op2.id)}, format='json'
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['assigned_to']['id'], str(self.op2.id))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assigned_to_id, self.op2.id)

    def test_cannot_reassign_to_operator_outside_workspace(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        outsider = User.objects.create_user(email='outsider@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=outsider, workspace=other_ws, role='WORKSPACE_OPERATOR')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/assign/', {'operator_id': str(outsider.id)}, format='json'
        )
        self.assertEqual(res.status_code, 404)
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assigned_to)

    def test_teammates_lists_only_workspace_operators(self):
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/teammates/')
        self.assertEqual(res.status_code, 200)
        ids = {t['id'] for t in res.data}
        self.assertEqual(ids, {str(self.op1.id), str(self.op2.id)})

    def test_close_sets_closed_at(self):
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/close/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data['closed_at'])
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, 'CLOSED')
        self.assertIsNotNone(self.conv.closed_at)

    def test_reopen_clears_closed_at_and_restores_open_status(self):
        self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/close/', format='json')
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/reopen/', format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'OPEN')
        self.assertIsNone(res.data['closed_at'])

    def test_reopen_rejected_when_not_closed(self):
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/reopen/', format='json')
        self.assertEqual(res.status_code, 400)


class ProductSearchAndSnapshotTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        Product.objects.create(workspace=self.workspace, name='Scented Candle', brand='Arom', price=890000, old_price=1120000)
        Product.objects.create(workspace=self.workspace, name='Ceramic Vase', brand='Arom', price=1450000)

    def test_search_filters_by_name(self):
        res = self.client.get('/api/v1/products/?search=Candle')
        self.assertEqual(res.status_code, 200)
        names = [p['name'] for p in res.data]
        self.assertEqual(names, ['Scented Candle'])

    def test_search_filters_by_brand(self):
        res = self.client.get('/api/v1/products/?search=Arom')
        self.assertEqual(len(res.data), 2)

    def test_discount_percent_computed(self):
        res = self.client.get('/api/v1/products/?search=Candle')
        self.assertEqual(res.data[0]['discount_percent'], 21)

    def test_share_product_snapshot_includes_discount_and_availability(self):
        visitor = Visitor.objects.create(project=self.project)
        conv = Conversation.objects.create(
            visitor=visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN,
        )
        product = Product.objects.get(name='Scented Candle')
        res = self.client.post(
            f'/api/v1/conversations/customer/{conv.id}/share_product/',
            {'product_id': str(product.id), 'client_message_id': 'p1'}, format='json',
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['metadata']['discount_percent'], 21)
        self.assertTrue(res.data['metadata']['is_available'])
        self.assertEqual(res.data['metadata']['currency'], 'IRT')
