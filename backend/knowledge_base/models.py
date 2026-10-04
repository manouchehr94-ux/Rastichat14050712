import uuid

from django.conf import settings
from django.db import models

from workspaces.models import Workspace


class KnowledgeBaseCategory(models.Model):
    """A workspace-owned, optionally-nested Knowledge Base category."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='kb_categories')
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, allow_unicode=True)
    description = models.TextField(blank=True, default='')
    sort_order = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'slug')
        ordering = ['sort_order', 'name']
        indexes = [
            models.Index(fields=['workspace', 'is_active'], name='kbcat_ws_active_idx'),
        ]

    def __str__(self):
        return f'{self.name} ({self.workspace_id})'


class KnowledgeBaseArticle(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        REVIEW = 'REVIEW', 'Review'
        PUBLISHED = 'PUBLISHED', 'Published'
        ARCHIVED = 'ARCHIVED', 'Archived'

    class Visibility(models.TextChoices):
        INTERNAL = 'INTERNAL', 'Internal only'
        CUSTOMER = 'CUSTOMER', 'Customer (this workspace only)'
        PUBLIC = 'PUBLIC', 'Public'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='kb_articles')
    category = models.ForeignKey(
        KnowledgeBaseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='articles',
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, allow_unicode=True)
    excerpt = models.CharField(max_length=500, blank=True, default='')
    # Markdown source — the only editing format. Never raw HTML: see
    # markdown_renderer.py, whose renderer only ever emits a small
    # hardcoded set of tags it builds itself, with all text content
    # escaped and all link/image URL schemes allowlisted. There is no
    # "sanitize this HTML" step because untrusted HTML never enters the
    # render path in the first place.
    body = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.INTERNAL)
    language = models.CharField(max_length=10, default='fa')
    tags = models.JSONField(default=list, blank=True)
    is_featured = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=100)
    # Normalized, denormalized search text (title + excerpt + body + tags +
    # category name, lowercased with Persian ی/ي and ک/ك folded and
    # whitespace collapsed) kept in sync by knowledge_base.search whenever
    # searchable content changes — see search.sync_search_text(). Read-only
    # from the API; never trust a client-supplied value here.
    search_text = models.TextField(blank=True, default='', editable=False)
    view_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    published_at = models.DateTimeField(null=True, blank=True)
    current_revision_number = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('workspace', 'slug')
        ordering = ['sort_order', '-updated_at']
        indexes = [
            models.Index(fields=['workspace', 'status', 'visibility'], name='kbart_ws_status_vis_idx'),
            models.Index(fields=['workspace', 'category'], name='kbart_ws_category_idx'),
        ]

    def __str__(self):
        return f'{self.title} ({self.workspace_id})'

    def is_publicly_visible(self):
        return self.status == self.Status.PUBLISHED and self.visibility in (
            self.Visibility.CUSTOMER, self.Visibility.PUBLIC,
        )


class KnowledgeBaseArticleRevision(models.Model):
    """An immutable snapshot of an article's editable content. Created on
    every meaningful save (see services.save_article_content) — restoring a
    past revision creates a NEW current revision rather than deleting or
    rewriting anything, so the full history is always reconstructable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    article = models.ForeignKey(KnowledgeBaseArticle, on_delete=models.CASCADE, related_name='revisions')
    revision_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    excerpt = models.CharField(max_length=500, blank=True, default='')
    body = models.TextField(blank=True, default='')
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    change_summary = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('article', 'revision_number')
        ordering = ['-revision_number']

    def __str__(self):
        return f'{self.article_id} rev {self.revision_number}'


class KnowledgeBaseArticleAttachment(models.Model):
    """A file attached to an article (typically an inline image). `workspace`
    is denormalized from the article at upload time so ownership can be
    checked with a single indexed filter, without a join, on every request
    that resolves an attachment by id — this is what makes cross-workspace
    attachment access a simple, cheap, always-applied query filter rather
    than something that could be forgotten on one code path.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='kb_attachments')
    article = models.ForeignKey(
        KnowledgeBaseArticle, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments',
    )
    file = models.FileField(upload_to='kb_attachments/%Y/%m/%d/')
    content_type = models.CharField(max_length=100, blank=True, default='')
    size = models.PositiveIntegerField(default=0)
    original_filename = models.CharField(max_length=255, blank=True, default='')
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workspace', 'article'], name='kbattach_ws_article_idx'),
        ]


class KnowledgeBaseArticleView(models.Model):
    """A lightweight view record — used only for the view_count counter and
    basic per-article popularity, never a full analytics warehouse (out of
    scope for this phase).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='kb_article_views')
    article = models.ForeignKey(KnowledgeBaseArticle, on_delete=models.CASCADE, related_name='view_events')
    visitor = models.ForeignKey('visitors.Visitor', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    viewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class KnowledgeBaseArticleFeedback(models.Model):
    """Visitor helpful/not-helpful feedback. One row per (article, visitor):
    a second submission from the same visitor updates the existing row
    (deterministic "last submission wins", never a duplicate row and never
    a silently-dropped second vote) — see services.submit_feedback.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='kb_article_feedback')
    article = models.ForeignKey(KnowledgeBaseArticle, on_delete=models.CASCADE, related_name='feedback')
    visitor = models.ForeignKey('visitors.Visitor', on_delete=models.CASCADE, related_name='+')
    conversation = models.ForeignKey(
        'conversations.Conversation', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    is_helpful = models.BooleanField()
    comment = models.CharField(max_length=1000, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('article', 'visitor')
        ordering = ['-created_at']
