from rest_framework.test import APITestCase

from conversations.models import Message
from . import services
from .tests_base import KBTestMixin


class ArticleSharingTests(KBTestMixin, APITestCase):
    def test_article_card_persists_as_snapshot(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        visitor = self.make_visitor(project)
        conv = self.make_conversation(ws, project, visitor)
        article = services.create_article(ws, operator, title='راهنمای مرجوعی', excerpt='خلاصه کوتاه', body='متن کامل')

        msg = services.share_article_to_conversation(article, conv, operator, 'client-1')

        self.assertEqual(msg.message_type, Message.MessageType.ARTICLE)
        self.assertEqual(msg.metadata['article']['title'], 'راهنمای مرجوعی')
        self.assertEqual(msg.metadata['article']['excerpt'], 'خلاصه کوتاه')
        self.assertEqual(msg.metadata['article']['article_id'], str(article.id))

    def test_changed_article_does_not_alter_old_message_snapshot(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        visitor = self.make_visitor(project)
        conv = self.make_conversation(ws, project, visitor)
        article = services.create_article(ws, operator, title='عنوان اصلی', excerpt='خلاصه اصلی', body='متن')

        msg = services.share_article_to_conversation(article, conv, operator, 'client-1')
        self.assertEqual(msg.metadata['article']['title'], 'عنوان اصلی')

        services.update_article_content(article, operator, title='عنوان تغییر یافته', excerpt='خلاصه تغییر یافته')

        msg.refresh_from_db()
        # The already-sent message must still show exactly what was shared —
        # never a live view of the now-changed article.
        self.assertEqual(msg.metadata['article']['title'], 'عنوان اصلی')
        self.assertEqual(msg.metadata['article']['excerpt'], 'خلاصه اصلی')

    def test_share_is_idempotent_on_client_message_id(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        visitor = self.make_visitor(project)
        conv = self.make_conversation(ws, project, visitor)
        article = services.create_article(ws, operator, title='عنوان', body='متن')

        msg1 = services.share_article_to_conversation(article, conv, operator, 'client-1')
        msg2 = services.share_article_to_conversation(article, conv, operator, 'client-1')
        self.assertEqual(msg1.id, msg2.id)
        self.assertEqual(Message.objects.filter(conversation=conv, message_type=Message.MessageType.ARTICLE).count(), 1)

    def test_share_rejects_cross_workspace_conversation(self):
        ws1 = self.make_workspace()
        operator1 = self.make_operator(ws1)
        article = services.create_article(ws1, operator1, title='عنوان', body='متن')

        ws2 = self.make_workspace()
        project2 = self.make_project(ws2)
        visitor2 = self.make_visitor(project2)
        conv2 = self.make_conversation(ws2, project2, visitor2)

        with self.assertRaises(services.KnowledgeBaseError):
            services.share_article_to_conversation(article, conv2, operator1, 'client-x')

    def test_share_api_endpoint(self):
        ws = self.make_workspace()
        project = self.make_project(ws)
        operator = self.make_operator(ws)
        visitor = self.make_visitor(project)
        conv = self.make_conversation(ws, project, visitor)
        article = services.create_article(ws, operator, title='عنوان', body='متن', visibility='CUSTOMER')

        self.login(self.client, operator)
        res = self.client.post(f'/api/v1/kb/articles/{article.id}/share/', {
            'conversation_id': str(conv.id), 'client_message_id': 'share-1',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.data['message_type'], 'ARTICLE')
