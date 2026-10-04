from decimal import Decimal
from django.utils import timezone
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor
from conversations.models import Conversation
from .models import Tag, ConversationTag, Note, CustomerProfile, CustomerOrder


class CustomerContextIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')

        self.other_platform = Platform.objects.create(name='P2')
        self.other_workspace = Workspace.objects.create(name='WS2', platform=self.other_platform)
        self.other_project = Project.objects.create(name='Pr2', workspace=self.other_workspace)
        self.other_operator = User.objects.create_user(email='op2@test.com', password='pass1234')
        WorkspaceMembership.objects.create(
            user=self.other_operator, workspace=self.other_workspace, role='WORKSPACE_OPERATOR'
        )

        self.visitor = Visitor.objects.create(project=self.project, name='Sara', mobile='0912')
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )

        res = self.client.post('/api/v1/auth/login/', {'email': 'op@test.com', 'password': 'pass1234'}, format='json')
        self.token = res.data['access']
        res2 = self.client.post('/api/v1/auth/login/', {'email': 'op2@test.com', 'password': 'pass1234'}, format='json')
        self.other_token = res2.data['access']

    # --- Tags ---

    def test_operator_can_create_and_attach_tag(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post('/api/v1/tags/', {'name': 'VIP', 'color': 'gold'}, format='json')
        self.assertEqual(res.status_code, 201)
        tag_id = res.data['id']

        attach = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/tags/', {'tag_id': tag_id}, format='json')
        self.assertEqual(attach.status_code, 201)
        self.assertEqual(attach.data[0]['name'], 'VIP')

    def test_tags_are_workspace_isolated(self):
        tag = Tag.objects.create(workspace=self.workspace, name='Loyal')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.other_token}')
        res = self.client.get('/api/v1/tags/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 0)

    def test_operator_cannot_attach_foreign_workspace_tag(self):
        foreign_tag = Tag.objects.create(workspace=self.other_workspace, name='Foreign')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/tags/', {'tag_id': str(foreign_tag.id)}, format='json'
        )
        self.assertEqual(res.status_code, 404)

    def test_operator_cannot_tag_foreign_conversation(self):
        tag = Tag.objects.create(workspace=self.workspace, name='VIP')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.other_token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/tags/', {'tag_id': str(tag.id)}, format='json'
        )
        self.assertEqual(res.status_code, 404)

    def test_detach_tag(self):
        tag = Tag.objects.create(workspace=self.workspace, name='VIP')
        ConversationTag.objects.create(conversation=self.conv, tag=tag, created_by=self.operator)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.delete(f'/api/v1/conversations/customer/{self.conv.id}/tags/', {'tag_id': str(tag.id)}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 0)

    # --- Notes ---

    def test_operator_can_create_note_with_author_and_timestamp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/notes/', {'body': 'Prefers warm scents'}, format='json'
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['created_by_email'], 'op@test.com')
        self.assertIsNotNone(res.data['created_at'])
        note = Note.objects.get(id=res.data['id'])
        self.assertEqual(note.created_by, self.operator)

    def test_note_over_max_length_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/notes/', {'body': 'x' * (Note.MAX_LENGTH + 1)}, format='json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Note.objects.count(), 0)

    def test_empty_note_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/notes/', {'body': '   '}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_notes_are_workspace_isolated(self):
        Note.objects.create(conversation=self.conv, body='Private', created_by=self.operator)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.other_token}')
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/notes/')
        self.assertEqual(res.status_code, 404)

    def test_notes_not_reachable_via_any_visitor_endpoint(self):
        # No session_token-authenticated route exists for notes at all —
        # confirm the endpoint requires operator auth, not visitor auth.
        res = self.client.post(f'/api/v1/conversations/customer/{self.conv.id}/notes/', {'body': 'hi'}, format='json')
        self.assertEqual(res.status_code, 401)

    # --- Customer context rollup ---

    def test_customer_context_degrades_gracefully_without_commerce_data(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/customer-context/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['order_count'], 0)
        self.assertEqual(Decimal(res.data['total_spent']), Decimal('0'))
        self.assertIsNone(res.data['score'])
        self.assertEqual(res.data['phone'], '0912')

    def test_customer_context_aggregates_orders_live(self):
        CustomerProfile.objects.create(visitor=self.visitor, workspace=self.workspace, location='Tehran', score=Decimal('4.9'))
        CustomerOrder.objects.create(
            workspace=self.workspace, visitor=self.visitor, product_name='Candle', price=890000,
            status=CustomerOrder.Status.DELIVERED, ordered_at=timezone.now(),
        )
        CustomerOrder.objects.create(
            workspace=self.workspace, visitor=self.visitor, product_name='Vase', price=1450000,
            status=CustomerOrder.Status.SHIPPED, ordered_at=timezone.now(),
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/customer-context/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['order_count'], 2)
        self.assertEqual(Decimal(res.data['total_spent']), Decimal('2340000'))
        self.assertEqual(res.data['location'], 'Tehran')
        self.assertEqual(Decimal(res.data['score']), Decimal('4.9'))

    def test_customer_context_isolated_across_workspaces(self):
        CustomerOrder.objects.create(
            workspace=self.workspace, visitor=self.visitor, product_name='Candle', price=890000,
            status=CustomerOrder.Status.DELIVERED, ordered_at=timezone.now(),
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.other_token}')
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/customer-context/')
        self.assertEqual(res.status_code, 404)

    def test_score_falls_back_to_live_conversation_rating_average_when_no_profile_score(self):
        # No CustomerProfile at all for this visitor.
        self.conv.rating = 4
        self.conv.save(update_fields=['rating'])
        second_conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.CLOSED, rating=5,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/customer-context/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(str(res.data['score'])), Decimal('4.5'))

    def test_manual_profile_score_overrides_the_computed_average(self):
        CustomerProfile.objects.create(visitor=self.visitor, workspace=self.workspace, score=Decimal('3.0'))
        self.conv.rating = 5
        self.conv.save(update_fields=['rating'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.get(f'/api/v1/conversations/customer/{self.conv.id}/customer-context/')
        self.assertEqual(Decimal(str(res.data['score'])), Decimal('3.0'))
