from django.contrib import admin

from .models import (
    KnowledgeBaseArticle, KnowledgeBaseArticleAttachment, KnowledgeBaseArticleFeedback,
    KnowledgeBaseArticleRevision, KnowledgeBaseArticleView, KnowledgeBaseCategory,
)

admin.site.register(KnowledgeBaseCategory)
admin.site.register(KnowledgeBaseArticle)
admin.site.register(KnowledgeBaseArticleRevision)
admin.site.register(KnowledgeBaseArticleAttachment)
admin.site.register(KnowledgeBaseArticleView)
admin.site.register(KnowledgeBaseArticleFeedback)
