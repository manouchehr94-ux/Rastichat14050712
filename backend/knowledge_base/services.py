"""Knowledge Base business logic: revision tracking, publish/restore/
archive, feedback, and the article-card snapshot shared into a
conversation. Views should never mutate KnowledgeBaseArticle*/Message rows
directly — everything with a history/audit/consistency requirement goes
through here, mirroring the conversations.services convention.
"""
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent

from .models import (
    KnowledgeBaseArticle, KnowledgeBaseArticleFeedback, KnowledgeBaseArticleRevision, KnowledgeBaseArticleView,
)
from .search import sync_search_text


class KnowledgeBaseError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _audit(actor, action, article, metadata=None):
    AuditEvent.objects.create(
        actor=actor, action=action, target_type='kb_article', target_id=str(article.id), metadata=metadata or {},
    )


def _next_revision_number(article):
    last = article.revisions.order_by('-revision_number').first()
    return (last.revision_number + 1) if last else 1


def create_article(workspace, actor, *, title, body='', excerpt='', category=None, status=None, visibility=None,
                    language='fa', tags=None, is_featured=False, sort_order=100, slug=None):
    from django.utils.text import slugify

    article = KnowledgeBaseArticle(
        workspace=workspace, category=category, title=title, body=body, excerpt=excerpt,
        status=status or KnowledgeBaseArticle.Status.DRAFT,
        visibility=visibility or KnowledgeBaseArticle.Visibility.INTERNAL,
        language=language or 'fa', tags=tags or [], is_featured=is_featured, sort_order=sort_order,
        created_by=actor, updated_by=actor,
    )
    article.slug = slug or slugify(title, allow_unicode=True) or f'article-{timezone.now().timestamp():.0f}'
    with transaction.atomic():
        article.save()
        _create_revision(article, actor, change_summary='Initial version')
        sync_search_text(article)
    _audit(actor, 'kb_article_created', article, {'title': title})
    return article


def _create_revision(article, actor, change_summary=''):
    rev = KnowledgeBaseArticleRevision.objects.create(
        article=article, revision_number=_next_revision_number(article),
        title=article.title, excerpt=article.excerpt, body=article.body,
        editor=actor, change_summary=change_summary[:500],
    )
    article.current_revision_number = rev.revision_number
    article.save(update_fields=['current_revision_number'])
    return rev


def update_article_content(article, actor, *, title=None, body=None, excerpt=None, category=None,
                            language=None, tags=None, is_featured=None, sort_order=None,
                            change_summary=''):
    """Updates editable content and always creates a new revision — the
    "every meaningful update creates a revision" requirement. Metadata-only
    changes (status/visibility transitions) go through publish_article/
    archive_article/set_visibility instead and do NOT create a content
    revision, since the content itself hasn't changed.
    """
    changed_content = False
    with transaction.atomic():
        if title is not None and title != article.title:
            article.title = title
            changed_content = True
        if body is not None and body != article.body:
            article.body = body
            changed_content = True
        if excerpt is not None and excerpt != article.excerpt:
            article.excerpt = excerpt
            changed_content = True
        if category is not None or category is False:
            article.category = None if category is False else category
        if language is not None:
            article.language = language
        if tags is not None:
            article.tags = tags
        if is_featured is not None:
            article.is_featured = is_featured
        if sort_order is not None:
            article.sort_order = sort_order
        article.updated_by = actor
        article.save()
        if changed_content:
            _create_revision(article, actor, change_summary=change_summary)
        sync_search_text(article)
    _audit(actor, 'kb_article_updated', article, {'changed_content': changed_content})
    return article


def restore_revision(article, actor, revision):
    """Restoring a past revision creates a NEW current revision with that
    revision's content — it never deletes or edits the revisions that came
    after it, so restoring old -> new -> old again is always possible and
    the full timeline stays intact.
    """
    if revision.article_id != article.id:
        raise KnowledgeBaseError('Revision does not belong to this article', 404)
    with transaction.atomic():
        article.title = revision.title
        article.excerpt = revision.excerpt
        article.body = revision.body
        article.updated_by = actor
        article.save()
        new_rev = _create_revision(
            article, actor, change_summary=f'Restored from revision {revision.revision_number}',
        )
        sync_search_text(article)
    _audit(actor, 'kb_article_restored', article, {'from_revision': revision.revision_number, 'new_revision': new_rev.revision_number})
    return article


def publish_article(article, actor, *, revision=None):
    """Publishes the article as-is, or — if `revision` is given — first
    restores that revision's content, then publishes. Either way the
    published article is always reproducible from a concrete revision
    (current_revision_number), never from in-memory-only state.
    """
    with transaction.atomic():
        if revision is not None:
            restore_revision(article, actor, revision)
        article.status = KnowledgeBaseArticle.Status.PUBLISHED
        article.published_by = actor
        article.published_at = timezone.now()
        article.updated_by = actor
        article.save(update_fields=['status', 'published_by', 'published_at', 'updated_by', 'updated_at'])
    _audit(actor, 'kb_article_published', article, {'revision': article.current_revision_number})
    return article


def archive_article(article, actor):
    with transaction.atomic():
        article.status = KnowledgeBaseArticle.Status.ARCHIVED
        article.updated_by = actor
        article.save(update_fields=['status', 'updated_by', 'updated_at'])
    _audit(actor, 'kb_article_archived', article, {})
    return article


def set_status(article, actor, status):
    if status not in KnowledgeBaseArticle.Status.values:
        raise KnowledgeBaseError('Invalid status', 400)
    if status == KnowledgeBaseArticle.Status.PUBLISHED:
        return publish_article(article, actor)
    if status == KnowledgeBaseArticle.Status.ARCHIVED:
        return archive_article(article, actor)
    with transaction.atomic():
        article.status = status
        article.updated_by = actor
        article.save(update_fields=['status', 'updated_by', 'updated_at'])
    _audit(actor, 'kb_article_status_changed', article, {'status': status})
    return article


def record_view(article, *, visitor=None, viewed_by=None):
    KnowledgeBaseArticleView.objects.create(
        workspace=article.workspace, article=article, visitor=visitor, viewed_by=viewed_by,
    )
    KnowledgeBaseArticle.objects.filter(pk=article.pk).update(view_count=article.view_count + 1)


def submit_feedback(article, visitor, *, is_helpful, comment='', conversation=None):
    """One feedback row per (article, visitor) — a resubmission updates the
    existing row deterministically (last submission wins) rather than
    creating a duplicate or silently rejecting the second vote.
    """
    feedback, _created = KnowledgeBaseArticleFeedback.objects.update_or_create(
        article=article, visitor=visitor,
        defaults={
            'workspace': article.workspace, 'is_helpful': is_helpful,
            'comment': (comment or '')[:1000], 'conversation': conversation,
        },
    )
    return feedback


def feedback_summary(article):
    qs = article.feedback.all()
    helpful = qs.filter(is_helpful=True).count()
    not_helpful = qs.filter(is_helpful=False).count()
    return {'helpful': helpful, 'not_helpful': not_helpful, 'total': helpful + not_helpful}


def share_article_to_conversation(article, conversation, actor, client_message_id, *, base_url=''):
    """Sends an article card into a conversation as a real Message (reusing
    the exact same Message model/history/broadcast path every other message
    type uses — never a parallel "card" system). The snapshot is frozen at
    send time into metadata: if the article is edited or deleted afterwards,
    this message keeps showing exactly what was shared, never a live view.
    """
    from conversations.models import Message
    from conversations.services import broadcast_ops_event

    if article.workspace_id != conversation.workspace_id:
        raise KnowledgeBaseError('Article does not belong to this conversation\'s workspace', 403)
    existing = Message.objects.filter(conversation=conversation, client_message_id=client_message_id).first()
    if existing:
        return existing
    snapshot = build_article_card_snapshot(article, base_url=base_url)
    msg = Message.objects.create(
        conversation=conversation, sender=actor, sender_type=Message.SenderType.USER,
        content=article.title, message_type=Message.MessageType.ARTICLE,
        metadata={'article': snapshot}, client_message_id=client_message_id,
    )
    broadcast_ops_event(conversation.id, 'conversation.article_shared', {'message_id': str(msg.id)})
    _broadcast_widget_message(conversation, msg)
    return msg


def _broadcast_widget_message(conversation, msg):
    import json

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if not layer:
        return
    payload = {
        'id': str(msg.id), 'sender_type': msg.sender_type, 'content': msg.content,
        'message_type': msg.message_type, 'metadata': msg.metadata, 'attachment_url': None,
        'client_message_id': msg.client_message_id, 'created_at': msg.created_at.isoformat(),
    }
    safe = json.loads(json.dumps(payload, default=str))
    async_to_sync(layer.group_send)(f'chat_{conversation.id}', {'type': 'chat.message', 'message': safe})


def build_article_card_snapshot(article, *, base_url=''):
    """A safe, self-contained snapshot of an article at share time —
    persisted into a Message's metadata (see conversations MessageType.
    ARTICLE) so that if the article is edited or deleted later, the
    already-sent message still shows exactly what was shared, never a
    live/mutated view of the current article.
    """
    image_url = ''
    first_attachment = article.attachments.first()
    if first_attachment and first_attachment.file:
        try:
            image_url = first_attachment.file.url
        except ValueError:
            image_url = ''
    return {
        'article_id': str(article.id),
        'title': article.title,
        'excerpt': article.excerpt,
        'category': article.category.name if article.category_id else '',
        'url': f'{base_url.rstrip("/")}/kb/{article.slug}' if base_url else f'/kb/{article.slug}',
        'image_url': image_url,
        'workspace_id': str(article.workspace_id),
        'snapshot_at': timezone.now().isoformat(),
    }
