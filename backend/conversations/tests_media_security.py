from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor, VisitorSession
from conversations.models import Conversation, Message


class MediaUploadValidationTests(TestCase):
    """Phase 9: signature-based validation must reject spoofed/oversized files."""

    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.operator = User.objects.create_user(email='sec-op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project, name='Sara')
        self.session = VisitorSession.objects.create(visitor=self.visitor)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )
        res = self.client.post('/api/v1/auth/login/', {'email': 'sec-op@test.com', 'password': 'pass1234'}, format='json')
        self.token = res.data['access']

    def test_operator_upload_rejects_content_mismatched_extension(self):
        # A plain-text payload dressed up as a .png with an image
        # content-type header must be rejected by signature sniffing,
        # not accepted on extension/content-type claims alone.
        fake = SimpleUploadedFile('evil.png', b'<script>alert(1)</script>', content_type='image/png')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/upload/',
            {'file': fake, 'message_type': 'IMAGE', 'client_message_id': 'sec1'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Message.objects.filter(client_message_id='sec1').exists())

    def test_operator_upload_accepts_real_png_signature(self):
        real_png = SimpleUploadedFile('pic.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 32, content_type='image/png')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/upload/',
            {'file': real_png, 'message_type': 'IMAGE', 'client_message_id': 'sec2'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)
        msg = Message.objects.get(client_message_id='sec2')
        # Storage filename is server-generated, never derived from client input.
        self.assertNotIn('pic', msg.attachment.name)
        self.assertTrue(msg.attachment.name.endswith('.png'))

    def test_operator_upload_rejects_oversized_file(self):
        big = SimpleUploadedFile(
            'pic.png', b'\x89PNG\r\n\x1a\n' + (b'\x00' * (9 * 1024 * 1024)), content_type='image/png'
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{self.conv.id}/upload/',
            {'file': big, 'message_type': 'IMAGE', 'client_message_id': 'sec3'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Message.objects.filter(client_message_id='sec3').exists())

    def test_visitor_upload_rejects_wrong_signature_for_declared_type(self):
        # PNG bytes submitted as a VOICE message must be rejected: the
        # signature doesn't match any recognized audio container.
        fake_voice = SimpleUploadedFile('note.webm', b'\x89PNG\r\n\x1a\n', content_type='audio/webm')
        res = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/upload/',
            {
                'file': fake_voice, 'message_type': 'VOICE', 'client_message_id': 'sec4',
                'session_token': str(self.session.token),
            },
            format='multipart',
        )
        self.assertEqual(res.status_code, 400)

    def test_visitor_upload_accepts_real_webm_signature(self):
        real_webm = SimpleUploadedFile(
            'note.webm', b'\x1a\x45\xdf\xa3' + b'\x00' * 32, content_type='audio/webm'
        )
        res = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/upload/',
            {
                'file': real_webm, 'message_type': 'VOICE', 'client_message_id': 'sec5',
                'session_token': str(self.session.token),
            },
            format='multipart',
        )
        self.assertEqual(res.status_code, 201)

    def test_operator_cannot_upload_to_foreign_workspace_conversation(self):
        other_platform = Platform.objects.create(name='P2')
        other_ws = Workspace.objects.create(name='WS2', platform=other_platform)
        other_project = Project.objects.create(name='Pr2', workspace=other_ws)
        other_visitor = Visitor.objects.create(project=other_project, name='Other')
        other_conv = Conversation.objects.create(
            visitor=other_visitor, workspace=other_ws, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )
        real_png = SimpleUploadedFile('pic.png', b'\x89PNG\r\n\x1a\n' + b'\x00' * 16, content_type='image/png')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')
        res = self.client.post(
            f'/api/v1/conversations/customer/{other_conv.id}/upload/',
            {'file': real_png, 'message_type': 'IMAGE', 'client_message_id': 'sec6'},
            format='multipart',
        )
        self.assertEqual(res.status_code, 404)


class RatingResubmissionTests(TestCase):
    """Phase 9 / audit finding: a second rating must not silently overwrite the first."""

    def setUp(self):
        self.client = APIClient()
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Pr1', workspace=self.workspace)
        self.visitor = Visitor.objects.create(project=self.project, name='Sara')
        self.session = VisitorSession.objects.create(visitor=self.visitor)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN,
        )

    def test_second_rating_is_rejected_not_silently_overwritten(self):
        first = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/rate/',
            {'session_token': str(self.session.token), 'rating': 5},
            format='json',
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            f'/api/v1/widget/conversations/{self.conv.id}/rate/',
            {'session_token': str(self.session.token), 'rating': 1, 'client_message_id': 'rating_attempt_2'},
            format='json',
        )
        self.assertEqual(second.status_code, 409)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.rating, 5)
        self.assertEqual(Message.objects.filter(conversation=self.conv, message_type='RATING').count(), 1)
