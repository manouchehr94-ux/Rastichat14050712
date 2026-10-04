import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from . import services
from .models import KnowledgeBaseArticleAttachment
from .tests_base import KBTestMixin

TINY_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)


class AttachmentTests(KBTestMixin, APITestCase):
    def test_attachment_validation_accepts_real_image(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='مقاله', body='متن')
        self.login(self.client, operator)

        upload = SimpleUploadedFile('photo.png', TINY_PNG, content_type='image/png')
        res = self.client.post(f'/api/v1/kb/articles/{article.id}/upload_attachment/', {'file': upload}, format='multipart')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(KnowledgeBaseArticleAttachment.objects.filter(article=article).count(), 1)

    def test_attachment_validation_rejects_fake_image(self):
        ws = self.make_workspace()
        operator = self.make_operator(ws)
        article = services.create_article(ws, operator, title='مقاله', body='متن')
        self.login(self.client, operator)

        fake = SimpleUploadedFile('evil.png', b'<script>alert(1)</script>', content_type='image/png')
        res = self.client.post(f'/api/v1/kb/articles/{article.id}/upload_attachment/', {'file': fake}, format='multipart')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(KnowledgeBaseArticleAttachment.objects.filter(article=article).count(), 0)

    def test_cross_workspace_attachment_denied(self):
        ws1 = self.make_workspace()
        ws2 = self.make_workspace()
        operator1 = self.make_operator(ws1)
        operator2 = self.make_operator(ws2)
        article1 = services.create_article(ws1, operator1, title='مقاله فضای‌کار ۱', body='متن')
        upload = SimpleUploadedFile('photo.png', TINY_PNG, content_type='image/png')
        attachment = KnowledgeBaseArticleAttachment.objects.create(
            workspace=ws1, article=article1, file=upload, content_type='image/png', size=len(TINY_PNG),
            uploaded_by=operator1,
        )

        self.login(self.client, operator2)
        # An operator in workspace 2 must never be able to attach to (or see
        # via the article detail) an article that belongs to workspace 1.
        res = self.client.get(f'/api/v1/kb/articles/{article1.id}/')
        self.assertEqual(res.status_code, 404)

        upload2 = SimpleUploadedFile('photo2.png', TINY_PNG, content_type='image/png')
        res = self.client.post(f'/api/v1/kb/articles/{article1.id}/upload_attachment/', {'file': upload2}, format='multipart')
        self.assertEqual(res.status_code, 404)
