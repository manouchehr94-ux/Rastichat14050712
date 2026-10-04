from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from common.mixins import WorkspaceScopedQuerysetMixin
from common.pagination import StandardPagination
from common.permissions import IsWorkspaceAdminOfObject, IsWorkspaceOperator, require_workspace_admin
from common.tenancy import resolve_operator_workspace
from conversations.models import Conversation
from projects.models import Project
from visitors.models import Visitor, VisitorSession

from . import services
from .attachments import UploadValidationError, validate_and_normalize_kb_upload
from .models import (
    KnowledgeBaseArticle, KnowledgeBaseArticleAttachment, KnowledgeBaseArticleRevision, KnowledgeBaseCategory,
)
from .search import search_articles
from .serializers import (
    KnowledgeBaseArticleAttachmentSerializer, KnowledgeBaseArticleFeedbackSerializer,
    KnowledgeBaseArticleRevisionSerializer, KnowledgeBaseArticleSerializer, KnowledgeBaseCategorySerializer,
    PublicKnowledgeBaseArticleDetailSerializer, PublicKnowledgeBaseArticleListSerializer,
    PublicKnowledgeBaseCategorySerializer,
)


class KnowledgeBaseCategoryViewSet(WorkspaceScopedQuerysetMixin, viewsets.ModelViewSet):
    """Any workspace operator can view the category tree; only a Workspace
    Owner/Admin can create, edit, delete, or reorder categories (spec
    section 18, tests #1/#2).
    """
    serializer_class = KnowledgeBaseCategorySerializer
    pagination_class = StandardPagination
    queryset = KnowledgeBaseCategory.objects.all()

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsWorkspaceOperator()]
        return [IsWorkspaceAdminOfObject()]

    def get_queryset(self):
        qs = super().get_queryset().select_related('parent')
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ('1', 'true', 'yes'))
        return qs

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        require_workspace_admin(request.user, workspace)
        serializer = self.get_serializer(data=request.data, context={**self.get_serializer_context(), 'workspace': workspace})
        serializer.is_valid(raise_exception=True)
        category = serializer.save(workspace=workspace, created_by=request.user, updated_by=request.user)
        return Response(self.get_serializer(category).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class KnowledgeBaseArticleViewSet(WorkspaceScopedQuerysetMixin, viewsets.ModelViewSet):
    """Any workspace operator can author/edit/publish/archive articles —
    collaborative, workspace-wide authoring, same trust boundary as
    automation rules/teams/queues. `visibility=INTERNAL` only ever governs
    exposure to VISITORS (see the public views below), never to operators.
    """
    serializer_class = KnowledgeBaseArticleSerializer
    pagination_class = StandardPagination
    permission_classes = [IsWorkspaceOperator]
    queryset = KnowledgeBaseArticle.objects.all()

    def get_queryset(self):
        qs = super().get_queryset().select_related('category').prefetch_related('attachments')
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        category_id = self.request.query_params.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)
        q = self.request.query_params.get('q')
        if q:
            qs = search_articles(qs, q)
        return qs

    def create(self, request, *args, **kwargs):
        workspace = resolve_operator_workspace(request.user, request.data.get('workspace'))
        category = None
        if request.data.get('category'):
            category = KnowledgeBaseCategory.objects.filter(id=request.data['category'], workspace=workspace).first()
            if category is None:
                return Response({'error': 'Category not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        article = services.create_article(
            workspace, request.user,
            title=request.data.get('title', '').strip() or 'بدون عنوان',
            body=request.data.get('body', ''), excerpt=request.data.get('excerpt', ''), category=category,
            status=request.data.get('status'), visibility=request.data.get('visibility'),
            language=request.data.get('language', 'fa'), tags=request.data.get('tags') or [],
            is_featured=bool(request.data.get('is_featured', False)), sort_order=request.data.get('sort_order', 100),
        )
        return Response(self.get_serializer(article).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        article = self.get_object()
        category = article.category
        if 'category' in request.data:
            category = False
            if request.data.get('category'):
                category = KnowledgeBaseCategory.objects.filter(id=request.data['category'], workspace=article.workspace).first()
                if category is None:
                    return Response({'error': 'Category not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        article = services.update_article_content(
            article, request.user,
            title=request.data.get('title'), body=request.data.get('body'), excerpt=request.data.get('excerpt'),
            category=category, language=request.data.get('language'), tags=request.data.get('tags'),
            is_featured=request.data.get('is_featured'), sort_order=request.data.get('sort_order'),
            change_summary=request.data.get('change_summary', ''),
        )
        return Response(self.get_serializer(article).data)

    partial_update = update

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        article = self.get_object()
        revision = None
        if request.data.get('revision_number'):
            revision = article.revisions.filter(revision_number=request.data['revision_number']).first()
            if revision is None:
                return Response({'error': 'Revision not found'}, status=status.HTTP_404_NOT_FOUND)
        article = services.publish_article(article, request.user, revision=revision)
        return Response(self.get_serializer(article).data)

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        article = services.archive_article(self.get_object(), request.user)
        return Response(self.get_serializer(article).data)

    @action(detail=True, methods=['post'])
    def set_status(self, request, pk=None):
        try:
            article = services.set_status(self.get_object(), request.user, request.data.get('status'))
        except services.KnowledgeBaseError as exc:
            return Response({'error': exc.message}, status=exc.status_code)
        return Response(self.get_serializer(article).data)

    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        source = self.get_object()
        clone = services.create_article(
            source.workspace, request.user, title=f'{source.title} (کپی)', body=source.body,
            excerpt=source.excerpt, category=source.category, status=KnowledgeBaseArticle.Status.DRAFT,
            visibility=source.visibility, language=source.language, tags=source.tags,
            is_featured=False, sort_order=source.sort_order,
        )
        return Response(self.get_serializer(clone).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def revisions(self, request, pk=None):
        article = self.get_object()
        return Response(KnowledgeBaseArticleRevisionSerializer(article.revisions.all(), many=True).data)

    @action(detail=True, methods=['post'], url_path='revisions/(?P<revision_number>[0-9]+)/restore')
    def restore_revision(self, request, pk=None, revision_number=None):
        article = self.get_object()
        revision = article.revisions.filter(revision_number=revision_number).first()
        if revision is None:
            return Response({'error': 'Revision not found'}, status=status.HTTP_404_NOT_FOUND)
        article = services.restore_revision(article, request.user, revision)
        return Response(self.get_serializer(article).data)

    @action(detail=True, methods=['post'])
    def share(self, request, pk=None):
        """Sends this article as a card into an active conversation —
        section 6's "send an article card" operator action.
        """
        article = self.get_object()
        conv_id = request.data.get('conversation_id')
        client_message_id = request.data.get('client_message_id')
        if not conv_id or not client_message_id:
            return Response({'error': 'conversation_id and client_message_id are required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            conversation = Conversation.objects.get(id=conv_id, workspace=article.workspace)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response({'error': 'Conversation not found in this workspace'}, status=status.HTTP_404_NOT_FOUND)
        base_url = request.data.get('base_url', '')
        msg = services.share_article_to_conversation(article, conversation, request.user, client_message_id, base_url=base_url)
        from conversations.serializers import MessageSerializer
        return Response(MessageSerializer(msg, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_attachment(self, request, pk=None):
        article = self.get_object()
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'Missing file'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            file = validate_and_normalize_kb_upload(file)
        except UploadValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        attachment = KnowledgeBaseArticleAttachment.objects.create(
            workspace=article.workspace, article=article, file=file, content_type=getattr(file, 'content_type', ''),
            size=file.size, original_filename=request.FILES.get('file').name[:255], uploaded_by=request.user,
        )
        return Response(
            KnowledgeBaseArticleAttachmentSerializer(attachment, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Public / customer-visible endpoints. Trust boundary is Project.public_key —
# the SAME boundary the widget itself already uses (visitors.views.InitVisitorView),
# never a plain workspace id, so a caller can only ever browse the KB of the
# storefront whose public embed key they hold.
# ---------------------------------------------------------------------------

def _resolve_public_workspace(request):
    project_key = request.query_params.get('project_key') or request.data.get('project_key')
    if not project_key:
        raise NotFound('project_key is required')
    try:
        project = Project.objects.select_related('workspace').get(public_key=project_key, is_active=True)
    except (Project.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound('Unknown project_key')
    return project.workspace


def _public_article_queryset(workspace):
    return KnowledgeBaseArticle.objects.filter(
        workspace=workspace, status=KnowledgeBaseArticle.Status.PUBLISHED,
        visibility__in=[KnowledgeBaseArticle.Visibility.CUSTOMER, KnowledgeBaseArticle.Visibility.PUBLIC],
    )


class PublicKnowledgeBaseCategoryListView(APIView):
    permission_classes = []
    pagination_class = StandardPagination

    def get(self, request):
        workspace = _resolve_public_workspace(request)
        qs = KnowledgeBaseCategory.objects.filter(workspace=workspace, is_active=True).order_by('sort_order', 'name')
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(PublicKnowledgeBaseCategorySerializer(page, many=True).data)


class PublicKnowledgeBaseArticleListView(APIView):
    permission_classes = []
    pagination_class = StandardPagination

    def get(self, request):
        workspace = _resolve_public_workspace(request)
        qs = _public_article_queryset(workspace).select_related('category')
        category_slug = request.query_params.get('category')
        if category_slug:
            qs = qs.filter(category__slug=category_slug)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(PublicKnowledgeBaseArticleListSerializer(page, many=True).data)


class PublicKnowledgeBaseSearchView(APIView):
    permission_classes = []
    pagination_class = StandardPagination
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'kb_search'

    def get(self, request):
        workspace = _resolve_public_workspace(request)
        q = request.query_params.get('q', '')
        qs = search_articles(_public_article_queryset(workspace).select_related('category'), q)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(PublicKnowledgeBaseArticleListSerializer(page, many=True).data)


def _get_public_article_or_404(request, slug):
    workspace = _resolve_public_workspace(request)
    article = _public_article_queryset(workspace).select_related('category').filter(slug=slug).first()
    if article is None:
        # Deliberately the same 404 whether the slug doesn't exist, belongs
        # to another workspace, or is a real but DRAFT/INTERNAL article —
        # existence of non-public content must never be inferable from the
        # response (spec section 5: "impossible to access through UUID/slug
        # guessing").
        raise NotFound('Article not found')
    return article


class PublicKnowledgeBaseArticleDetailView(APIView):
    permission_classes = []

    def get(self, request, slug):
        article = _get_public_article_or_404(request, slug)
        visitor = _resolve_optional_visitor(request)
        services.record_view(article, visitor=visitor)
        return Response(PublicKnowledgeBaseArticleDetailSerializer(article).data)


class PublicKnowledgeBaseRelatedArticlesView(APIView):
    permission_classes = []

    def get(self, request, slug):
        article = _get_public_article_or_404(request, slug)
        qs = _public_article_queryset(article.workspace).exclude(id=article.id)
        if article.category_id:
            qs = qs.filter(category_id=article.category_id)
        else:
            qs = qs.none()
        qs = qs.select_related('category').order_by('sort_order', '-published_at')[:5]
        return Response(PublicKnowledgeBaseArticleListSerializer(qs, many=True).data)


def _resolve_optional_visitor(request):
    token = request.query_params.get('session_token') or request.data.get('session_token')
    if not token:
        return None
    try:
        return VisitorSession.objects.select_related('visitor').get(token=token).visitor
    except (VisitorSession.DoesNotExist, DjangoValidationError, ValueError):
        return None


class PublicKnowledgeBaseFeedbackView(APIView):
    """Feedback requires a real Visitor identity (via the same session_token
    every other widget-facing endpoint already uses) so one vote per visitor
    per article can be enforced deterministically.
    """
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'kb_feedback'

    def post(self, request, slug):
        article = _get_public_article_or_404(request, slug)
        try:
            session = VisitorSession.objects.select_related('visitor').get(token=request.data.get('session_token'))
        except (VisitorSession.DoesNotExist, DjangoValidationError, ValueError):
            return Response({'error': 'Invalid session'}, status=status.HTTP_401_UNAUTHORIZED)
        is_helpful = request.data.get('is_helpful')
        if is_helpful is None:
            return Response({'error': 'is_helpful is required'}, status=status.HTTP_400_BAD_REQUEST)
        conversation = None
        conv_id = request.data.get('conversation_id')
        if conv_id:
            conversation = Conversation.objects.filter(id=conv_id, workspace=article.workspace, visitor=session.visitor).first()
        feedback = services.submit_feedback(
            article, session.visitor, is_helpful=bool(is_helpful),
            comment=request.data.get('comment', ''), conversation=conversation,
        )
        return Response(KnowledgeBaseArticleFeedbackSerializer(feedback).data, status=status.HTTP_200_OK)


class ArticleFeedbackSummaryView(APIView):
    """Workspace-operator-inspectable aggregated feedback (spec section 7 —
    no full analytics dashboard, just the helpful/not-helpful counts).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            article = KnowledgeBaseArticle.objects.get(pk=pk)
        except (KnowledgeBaseArticle.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        from common.permissions import user_has_workspace_role
        if not user_has_workspace_role(request.user, article.workspace_id, ['WORKSPACE_OWNER', 'WORKSPACE_ADMIN', 'WORKSPACE_OPERATOR']):
            raise PermissionDenied('Not a member of this workspace')
        return Response(services.feedback_summary(article))
