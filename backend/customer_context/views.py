from decimal import Decimal
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Avg, Sum
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsWorkspaceOperator
from common.tenancy import resolve_operator_workspace
from conversations.models import Conversation
from .models import Tag, ConversationTag, Note, CustomerProfile, CustomerOrder
from .serializers import TagSerializer, NoteSerializer, CustomerOrderSerializer


def _get_operator_conversation(user, conv_id):
    """Workspace-scoped conversation lookup shared by every endpoint below.

    Raises Conversation.DoesNotExist for both "not found" and "not yours" —
    callers turn that into a 404, never leaking whether a conversation
    exists in a workspace the requester doesn't belong to.
    """
    return Conversation.objects.get(
        id=conv_id, workspace__memberships__user=user, type=Conversation.Type.CUSTOMER,
    )


class TagViewSet(viewsets.ModelViewSet):
    """Workspace-owned tag catalog (create/list/rename/delete tag definitions)."""
    serializer_class = TagSerializer
    permission_classes = [IsWorkspaceOperator]

    def get_queryset(self):
        return Tag.objects.filter(workspace__memberships__user=self.request.user)

    def perform_create(self, serializer):
        workspace_id = self.request.data.get('workspace') or self.request.data.get('workspace_id')
        workspace = resolve_operator_workspace(self.request.user, workspace_id)
        serializer.save(workspace=workspace)


class ConversationTagsView(APIView):
    """Attach/detach/list the tags applied to one conversation."""
    permission_classes = [IsWorkspaceOperator]

    def get(self, request, conv_id):
        try:
            conv = _get_operator_conversation(request.user, conv_id)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        tags = Tag.objects.filter(conversation_tags__conversation=conv)
        return Response(TagSerializer(tags, many=True).data)

    def post(self, request, conv_id):
        try:
            conv = _get_operator_conversation(request.user, conv_id)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            tag = Tag.objects.get(id=request.data.get('tag_id'), workspace=conv.workspace)
        except (Tag.DoesNotExist, DjangoValidationError, ValueError):
            return Response({'error': 'Tag not found'}, status=status.HTTP_404_NOT_FOUND)
        ConversationTag.objects.get_or_create(conversation=conv, tag=tag, defaults={'created_by': request.user})
        tags = Tag.objects.filter(conversation_tags__conversation=conv)
        return Response(TagSerializer(tags, many=True).data, status=status.HTTP_201_CREATED)

    def delete(self, request, conv_id):
        try:
            conv = _get_operator_conversation(request.user, conv_id)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        tag_id = request.data.get('tag_id') or request.query_params.get('tag_id')
        ConversationTag.objects.filter(conversation=conv, tag_id=tag_id).delete()
        tags = Tag.objects.filter(conversation_tags__conversation=conv)
        return Response(TagSerializer(tags, many=True).data)


class ConversationNotesView(APIView):
    """Discrete, authored, timestamped private operator notes on a conversation.

    Never reachable from any visitor-facing (session_token-authenticated)
    route — only IsWorkspaceOperator-gated, workspace-scoped access exists.
    """
    permission_classes = [IsWorkspaceOperator]

    def get(self, request, conv_id):
        try:
            conv = _get_operator_conversation(request.user, conv_id)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        notes = conv.operator_notes.all()
        return Response(NoteSerializer(notes, many=True).data)

    def post(self, request, conv_id):
        try:
            conv = _get_operator_conversation(request.user, conv_id)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        body = (request.data.get('body') or '').strip()
        if not body:
            return Response({'error': 'Empty note'}, status=status.HTTP_400_BAD_REQUEST)
        if len(body) > Note.MAX_LENGTH:
            return Response(
                {'error': f'Note exceeds {Note.MAX_LENGTH} characters'}, status=status.HTTP_400_BAD_REQUEST
            )
        note = Note.objects.create(conversation=conv, body=body, created_by=request.user, updated_by=request.user)
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)


class CustomerContextView(APIView):
    """Read-only rollup of a customer's profile/orders/tags for the operator info panel.

    Order count and total spend are always computed live from
    `CustomerOrder` rows rather than stored/denormalized, so they can never
    drift out of sync. Tenants with no local order data simply see
    order_count=0 / total_spent=0 rather than an error — this endpoint must
    degrade gracefully for workspaces that aren't e-commerce stores.
    """
    permission_classes = [IsWorkspaceOperator]

    def get(self, request, conv_id):
        try:
            conv = _get_operator_conversation(request.user, conv_id)
        except (Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        visitor = conv.visitor
        if visitor is None:
            return Response({'error': 'No visitor associated with this conversation'}, status=status.HTTP_404_NOT_FOUND)

        profile = CustomerProfile.objects.filter(visitor=visitor, workspace=conv.workspace).first()
        orders = CustomerOrder.objects.filter(visitor=visitor, workspace=conv.workspace)
        total_spent = orders.aggregate(total=Sum('price'))['total'] or Decimal('0')
        tags = Tag.objects.filter(conversation_tags__conversation=conv)

        # A manually-set profile score always wins (an operator's deliberate
        # override); otherwise fall back to a live average of this visitor's
        # own conversation ratings in this workspace — real data, never a
        # fabricated default, and never stored/denormalized.
        score = profile.score if profile and profile.score is not None else None
        if score is None:
            avg = Conversation.objects.filter(
                visitor=visitor, workspace=conv.workspace, rating__isnull=False,
            ).aggregate(avg=Avg('rating'))['avg']
            score = round(avg, 1) if avg is not None else None

        return Response({
            'visitor_id': str(visitor.id),
            'name': visitor.name,
            'email': visitor.email,
            'phone': visitor.mobile,
            'location': profile.location if profile else '',
            'customer_since': visitor.created_at,
            'order_count': orders.count(),
            'total_spent': total_spent,
            'score': score,
            'tags': TagSerializer(tags, many=True).data,
            'recent_orders': CustomerOrderSerializer(orders[:10], many=True).data,
        })
