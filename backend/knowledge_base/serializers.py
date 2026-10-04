from rest_framework import serializers

from .markdown_renderer import render_markdown
from .models import (
    KnowledgeBaseArticle, KnowledgeBaseArticleAttachment, KnowledgeBaseArticleFeedback,
    KnowledgeBaseArticleRevision, KnowledgeBaseCategory,
)
from .services import feedback_summary


class KnowledgeBaseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBaseCategory
        fields = [
            'id', 'workspace', 'parent', 'name', 'slug', 'description', 'sort_order', 'is_active',
            'created_by', 'updated_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'workspace', 'created_by', 'updated_by', 'created_at', 'updated_at']

    def validate_parent(self, parent):
        if parent is None:
            return parent
        workspace = self.context.get('workspace')
        if workspace and parent.workspace_id != workspace.id:
            raise serializers.ValidationError('Parent category must belong to the same workspace')
        node = parent
        seen = set()
        instance_id = self.instance.id if self.instance else None
        while node is not None:
            if node.id == instance_id:
                raise serializers.ValidationError('A category cannot be its own ancestor')
            if node.id in seen:
                break
            seen.add(node.id)
            node = node.parent
        return parent


class KnowledgeBaseArticleAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeBaseArticleAttachment
        fields = ['id', 'article', 'url', 'content_type', 'size', 'original_filename', 'uploaded_by', 'created_at']
        read_only_fields = fields

    def get_url(self, obj):
        request = self.context.get('request')
        try:
            url = obj.file.url
        except ValueError:
            return ''
        return request.build_absolute_uri(url) if request else url


class KnowledgeBaseArticleRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBaseArticleRevision
        fields = ['id', 'article', 'revision_number', 'title', 'excerpt', 'body', 'editor', 'change_summary', 'created_at']
        read_only_fields = fields


class KnowledgeBaseArticleSerializer(serializers.ModelSerializer):
    rendered_body = serializers.SerializerMethodField()
    attachments = KnowledgeBaseArticleAttachmentSerializer(many=True, read_only=True)
    feedback_summary = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeBaseArticle
        fields = [
            'id', 'workspace', 'category', 'title', 'slug', 'excerpt', 'body', 'rendered_body',
            'status', 'visibility', 'language', 'tags', 'is_featured', 'sort_order', 'view_count',
            'current_revision_number', 'attachments', 'feedback_summary',
            'created_by', 'updated_by', 'published_by', 'published_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'workspace', 'slug', 'view_count', 'current_revision_number', 'attachments', 'feedback_summary',
            'created_by', 'updated_by', 'published_by', 'published_at', 'created_at', 'updated_at',
        ]

    def get_rendered_body(self, obj):
        return render_markdown(obj.body)

    def get_feedback_summary(self, obj):
        return feedback_summary(obj)

    def validate_category(self, category):
        if category is None:
            return category
        workspace = self.context.get('workspace') or (self.instance.workspace if self.instance else None)
        if workspace and category.workspace_id != workspace.id:
            raise serializers.ValidationError('Category must belong to the same workspace')
        return category


class PublicKnowledgeBaseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBaseCategory
        fields = ['id', 'name', 'slug', 'description', 'sort_order']


class PublicKnowledgeBaseArticleListSerializer(serializers.ModelSerializer):
    category = PublicKnowledgeBaseCategorySerializer(read_only=True)

    class Meta:
        model = KnowledgeBaseArticle
        fields = ['id', 'slug', 'title', 'excerpt', 'category', 'is_featured', 'tags', 'published_at']


class PublicKnowledgeBaseArticleDetailSerializer(serializers.ModelSerializer):
    category = PublicKnowledgeBaseCategorySerializer(read_only=True)
    rendered_body = serializers.SerializerMethodField()
    feedback_summary = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeBaseArticle
        fields = [
            'id', 'slug', 'title', 'excerpt', 'rendered_body', 'category', 'tags', 'is_featured',
            'view_count', 'feedback_summary', 'published_at',
        ]

    def get_rendered_body(self, obj):
        return render_markdown(obj.body)

    def get_feedback_summary(self, obj):
        return feedback_summary(obj)


class KnowledgeBaseArticleFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBaseArticleFeedback
        fields = ['id', 'article', 'is_helpful', 'comment', 'created_at', 'updated_at']
        read_only_fields = ['id', 'article', 'created_at', 'updated_at']

    def validate_comment(self, value):
        return (value or '')[:1000]
