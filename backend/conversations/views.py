import json
from django.db.models import Max, F, Q
from django.db import models
from rest_framework import viewsets, mixins, status, permissions as drf_permissions
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.throttling import ScopedRateThrottle
from .models import Conversation, Message, MessageReceipt, Assignment, PriorityChange
from .serializers import ConversationSerializer, MessageSerializer, AssignmentSerializer
from .media_validation import validate_and_normalize_upload, UploadValidationError
from . import services as conv_services
from common.pagination import StandardPagination
from common.permissions import IsWorkspaceOperator, IsWorkspaceAdmin, IsPlatformSupportAgent, user_can_supervise_workspace
from common.tenancy import resolve_operator_workspace
from visitors.models import Visitor, VisitorSession
from catalog.models import Product
from teams.models import TeamMembership
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from audit.models import AuditEvent
from .branding import build_widget_branding

User = get_user_model()


def _service_error_response(exc):
    return Response({'error': exc.message}, status=exc.status_code)


def _broadcast(conv_id, message_data, group_prefix='chat'):
    # The channel layer serializes with msgpack, which chokes on non-primitive
    # types (e.g. the UUID a DRF PrimaryKeyRelatedField returns for `conversation`).
    # Round-tripping through JSON guarantees a msgpack-safe, plain-primitive payload.
    safe_data = json.loads(json.dumps(dict(message_data), default=str))
    async_to_sync(get_channel_layer().group_send)(
        f"{group_prefix}_{conv_id}", {'type': 'chat.message', 'message': safe_data}
    )


def _broadcast_seen(conv_id, reader, group_prefix='chat'):
    async_to_sync(get_channel_layer().group_send)(
        f"{group_prefix}_{conv_id}", {'type': 'message.seen', 'reader': reader}
    )


def _broadcast_branding(conv):
    async_to_sync(get_channel_layer().group_send)(
        f"chat_{conv.id}", {'type': 'branding.updated', 'branding': build_widget_branding(conv)}
    )


class CustomerConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer
    permission_classes = [IsWorkspaceOperator]
    pagination_class = None  # existing frontends depend on the plain-array shape; unchanged from earlier stages
    throttle_scope = 'media_upload'  # only consulted by the `upload` action's ScopedRateThrottle

    def get_queryset(self):
        qs = Conversation.objects.filter(workspace__memberships__user=self.request.user, type=Conversation.Type.CUSTOMER)
        params = self.request.query_params
        if params.get('queue'):
            qs = qs.filter(queue_id=params['queue'])
        if params.get('team'):
            qs = qs.filter(team_id=params['team'])
        if params.get('priority'):
            qs = qs.filter(priority=params['priority'])
        if params.get('assignee'):
            qs = qs.filter(assigned_to_id=params['assignee'])
        if params.get('unassigned') in ('1', 'true', 'True'):
            qs = qs.filter(assigned_to__isnull=True)
        if params.get('mine') in ('1', 'true', 'True'):
            qs = qs.filter(assigned_to=self.request.user)
        sla_status = params.get('sla_status')
        if sla_status == 'breached':
            qs = qs.filter(sla__isnull=False).filter(
                models.Q(sla__first_response_breached_at__isnull=False)
                | models.Q(sla__next_response_breached_at__isnull=False)
                | models.Q(sla__resolution_breached_at__isnull=False)
            )
        elif sla_status == 'approaching':
            qs = qs.filter(sla__isnull=False).filter(
                models.Q(sla__first_response_approaching_notified_at__isnull=False, sla__first_responded_at__isnull=True)
                | models.Q(sla__next_response_approaching_notified_at__isnull=False)
                | models.Q(sla__resolution_approaching_notified_at__isnull=False, sla__resolved_at__isnull=True)
            )
        # Inbox order must follow the newest customer-visible message,
        # not conversation creation time or the clock portion alone.
        qs = qs.distinct()
        return qs.annotate(
            last_message_at=Max(
                'messages__created_at',
                filter=~Q(messages__message_type=Message.MessageType.INTERNAL_NOTE),
            )
        ).order_by(F('last_message_at').desc(nulls_last=True), '-created_at')

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        # Internal notes are included here (operator-only view) so they render
        # inline with the customer conversation, visually distinguished by
        # message_type — they're excluded only from the visitor-facing
        # WidgetMessageListView and from the customer-context summaries below.
        conv = self.get_object()
        msgs = conv.messages.all().order_by('created_at')
        return Response(MessageSerializer(msgs, many=True, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        conv = self.get_object()
        for msg in conv.messages.exclude(receipts__user=request.user).exclude(message_type=Message.MessageType.INTERNAL_NOTE):
            MessageReceipt.objects.create(message=msg, user=request.user)
        _broadcast_seen(conv.id, 'USER')
        return Response(status=200)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Assign (or reassign) the conversation to an operator.

        With no `operator_id`, self-assigns the requesting operator (the
        existing "واگذاری به من" behavior). With an `operator_id`, reassigns
        to that operator instead — but only if they're a member of the same
        workspace, so an operator can never assign a conversation to someone
        outside their own tenant.
        """
        conv = self.get_object()
        operator_id = request.data.get('operator_id')
        try:
            if operator_id:
                conv = conv_services.assign_to_agent(conv, request.user, operator_id)
            else:
                conv = conv_services.assign_to_self(conv, request.user)
        except conv_services.ConversationServiceError as exc:
            return _service_error_response(exc)
        _broadcast_branding(conv)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """A human agent claims an unassigned conversation for themselves —
        concurrency-safe: only the first of two simultaneous claimants wins.
        """
        from queues.services import claim_conversation, AssignmentError
        conv = self.get_object()
        try:
            conv = claim_conversation(conv, request.user)
        except AssignmentError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        _broadcast_branding(conv)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """Transfer to a different team. The receiving team must claim/assign
        explicitly — a transfer never silently keeps the old direct assignee.
        """
        conv = self.get_object()
        team_id = request.data.get('team_id')
        if not team_id:
            return Response({'error': 'team_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            conv = conv_services.transfer_team(conv, request.user, team_id, reason=request.data.get('reason', ''))
        except conv_services.ConversationServiceError as exc:
            return _service_error_response(exc)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def unassign(self, request, pk=None):
        conv = self.get_object()
        conv = conv_services.unassign(conv, request.user, reason=request.data.get('reason', ''))
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def return_to_queue(self, request, pk=None):
        conv = self.get_object()
        conv = conv_services.return_to_queue(conv, request.user, reason=request.data.get('reason', ''))
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def escalate(self, request, pk=None):
        conv = self.get_object()
        try:
            conv = conv_services.escalate(conv, request.user, reason=request.data.get('reason', ''))
        except conv_services.ConversationServiceError as exc:
            return _service_error_response(exc)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def set_priority(self, request, pk=None):
        conv = self.get_object()
        priority = request.data.get('priority')
        try:
            conv = conv_services.set_priority(conv, request.user, priority, reason=request.data.get('reason', ''))
        except conv_services.ConversationServiceError as exc:
            return _service_error_response(exc)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['get'])
    def assignment_history(self, request, pk=None):
        conv = self.get_object()
        history = conv.assignment_history.all()
        return Response(AssignmentSerializer(history, many=True).data)

    @action(detail=True, methods=['get', 'post'])
    def internal_notes(self, request, pk=None):
        """Operator-only collaboration notes — never delivered to the widget
        (see `.services.broadcast_ops_event`, which only ever targets the
        operator-only `chat_ops_<id>` group).
        """
        from collaboration.models import Mention
        from collaboration.serializers import InternalNoteSerializer
        from notifications.models import Notification

        conv = self.get_object()
        if request.method == 'GET':
            notes = conv.messages.filter(message_type=Message.MessageType.INTERNAL_NOTE).order_by('created_at')
            return Response(InternalNoteSerializer(notes, many=True, context={'request': request}).data)

        body = (request.data.get('content') or '').strip()
        if not body:
            return Response({'error': 'Empty note'}, status=status.HTTP_400_BAD_REQUEST)
        client_msg_id = request.data.get('client_message_id')
        if not client_msg_id:
            return Response({'error': 'Missing client_message_id'}, status=status.HTTP_400_BAD_REQUEST)
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)

        mentioned_ids = request.data.get('mentioned_user_ids') or []
        eligible_mentions = list(User.objects.filter(id__in=mentioned_ids, workspace_memberships__workspace=conv.workspace).distinct())

        note = Message.objects.create(
            conversation=conv, sender=request.user, sender_type=Message.SenderType.USER,
            content=body, client_message_id=client_msg_id, message_type=Message.MessageType.INTERNAL_NOTE,
        )
        for user in eligible_mentions:
            Mention.objects.create(note=note, mentioned_user=user, created_by=request.user)
            if user.id != request.user.id:
                from notifications.services import notify
                notify(
                    user, conv.workspace, Notification.EventType.MENTIONED,
                    'در یک یادداشت داخلی به شما اشاره شد', {'conversation_id': str(conv.id), 'note_id': str(note.id)},
                )
        AuditEvent.objects.create(
            actor=request.user, action='internal_note_created', target_type='conversation', target_id=str(conv.id),
            metadata={'mentioned_count': len(eligible_mentions)},
        )
        data = InternalNoteSerializer(note, context={'request': request}).data
        conv_services.broadcast_ops_event(conv.id, 'conversation.internal_note_created', {'note': data})
        from automations.events import publish_event
        publish_event(
            'INTERNAL_NOTE_CREATED', conv.workspace_id, conversation_id=conv.id,
            actor_id=request.user.id, actor_type='USER', payload={'content': body},
        )
        return Response(data, status=201)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        conv = self.get_object()
        conv = conv_services.close_conversation(conv, actor=request.user)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        conv = self.get_object()
        try:
            conv = conv_services.reopen_conversation(conv, actor=request.user)
        except conv_services.ConversationServiceError as exc:
            return Response({'error': exc.message}, status=exc.status_code)
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['get'])
    def teammates(self, request, pk=None):
        """Workspace operators this conversation can be (re)assigned to."""
        conv = self.get_object()
        operators = User.objects.filter(workspace_memberships__workspace=conv.workspace).distinct()
        return Response([
            {'id': str(u.id), 'display_name': u.display_name or u.email.split('@')[0], 'email': u.email}
            for u in operators
        ])

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser],
            throttle_classes=[ScopedRateThrottle])
    def upload(self, request, pk=None):
        conv = self.get_object()
        message_type = request.data.get('message_type', Message.MessageType.IMAGE)
        if message_type not in (Message.MessageType.IMAGE, Message.MessageType.VOICE):
            return Response({'error': 'Invalid message_type'}, status=status.HTTP_400_BAD_REQUEST)
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'Missing file'}, status=status.HTTP_400_BAD_REQUEST)
        client_msg_id = request.data.get('client_message_id')
        if not client_msg_id:
            return Response({'error': 'Missing client_message_id'}, status=status.HTTP_400_BAD_REQUEST)
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)
        try:
            file = validate_and_normalize_upload(file, message_type)
        except UploadValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        metadata = {}
        caption = (request.data.get('caption') or '').strip()
        if caption:
            metadata['caption'] = caption
        duration = request.data.get('duration')
        if duration:
            metadata['duration'] = duration
        msg = Message.objects.create(
            conversation=conv, sender=request.user, sender_type=Message.SenderType.USER,
            content='', client_message_id=client_msg_id, message_type=message_type,
            metadata=metadata, attachment=file,
        )
        data = MessageSerializer(msg, context={'request': request}).data
        _broadcast(conv.id, data)
        return Response(data, status=201)

    @action(detail=True, methods=['post'])
    def share_product(self, request, pk=None):
        conv = self.get_object()
        client_msg_id = request.data.get('client_message_id')
        if not client_msg_id:
            return Response({'error': 'Missing client_message_id'}, status=status.HTTP_400_BAD_REQUEST)
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)
        try:
            product = Product.objects.get(id=request.data.get('product_id'), workspace=conv.workspace)
        except (Product.DoesNotExist, ValueError, DjangoValidationError):
            return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)
        discount_percent = 0
        if product.old_price and product.old_price > product.price:
            discount_percent = round((1 - (product.price / product.old_price)) * 100)
        metadata = {
            'product_id': str(product.id), 'brand': product.brand, 'name': product.name,
            'price': str(product.price), 'old_price': str(product.old_price) if product.old_price else None,
            'currency': product.currency, 'discount_percent': discount_percent,
            'rating': float(product.rating), 'reviews_count': product.reviews_count, 'image': product.image,
            'product_url': product.product_url, 'is_available': product.is_available,
        }
        msg = Message.objects.create(
            conversation=conv, sender=request.user, sender_type=Message.SenderType.USER,
            content='', client_message_id=client_msg_id, message_type=Message.MessageType.PRODUCT,
            metadata=metadata,
        )
        data = MessageSerializer(msg, context={'request': request}).data
        _broadcast(conv.id, data)
        return Response(data, status=201)

    @action(detail=True, methods=['post'])
    def request_rating(self, request, pk=None):
        conv = self.get_object()
        client_msg_id = request.data.get('client_message_id') or f'rating_req_{conv.id}'
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)
        msg = Message.objects.create(
            conversation=conv, sender=request.user, sender_type=Message.SenderType.USER,
            content='', client_message_id=client_msg_id, message_type=Message.MessageType.RATING_REQUEST,
        )
        data = MessageSerializer(msg, context={'request': request}).data
        _broadcast(conv.id, data)
        return Response(data, status=201)

class StartCustomerChatView(APIView):
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'widget_start'
    def post(self, request):
        from django.core.exceptions import ValidationError
        from django.db import transaction
        try:
            session = VisitorSession.objects.get(token=request.data.get('session_token'))
        except (VisitorSession.DoesNotExist, ValidationError):
            return Response({'error': 'Invalid session'}, status=status.HTTP_401_UNAUTHORIZED)
        with transaction.atomic():
            conv, created = Conversation.objects.get_or_create(visitor=session.visitor, workspace=session.visitor.project.workspace, type=Conversation.Type.CUSTOMER, status=Conversation.Status.OPEN)
            if not created and conv.status == 'CLOSED': conv.status = 'OPEN'; conv.save()
            if created:
                from queues.services import route_new_conversation
                from sla.services import apply_sla
                from automations.events import publish_event
                # Routing/SLA must be persisted BEFORE CONVERSATION_CREATED is
                # published — an automation condition on conversation.queue_id,
                # operational.queue_has_capacity, or conversation.sla_state must
                # see the conversation's real, final creation-time state, not an
                # unrouted, SLA-less intermediate one. publish_event() itself only
                # schedules delivery via transaction.on_commit(), so wrapping the
                # whole sequence in one atomic block also means a failure here
                # (e.g. routing/SLA raising) rolls back the conversation creation
                # too, rather than leaving a misleading published event with no
                # backing conversation state.
                conv = route_new_conversation(conv)
                apply_sla(conv)
                publish_event('CONVERSATION_CREATED', conv.workspace_id, conversation_id=conv.id, actor_type='SYSTEM')
        if created:
            conv_services.broadcast_ops_event(conv.id, 'conversation.queued', conv_services.conversation_summary(conv))
        data = ConversationSerializer(conv).data
        data['branding'] = build_widget_branding(conv)
        return Response(data, status=200)

class MessageListView(APIView):
    permission_classes = [IsWorkspaceOperator]
    def get(self, request, conv_id):
        try:
            conv = Conversation.objects.get(id=conv_id, workspace__memberships__user=request.user)
        except Conversation.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        msgs = conv.messages.all().order_by('created_at')
        return Response(MessageSerializer(msgs, many=True, context={'request': request}).data)

class SendMessageView(APIView):
    permission_classes = [IsWorkspaceOperator]
    def post(self, request, conv_id):
        content = request.data.get('content', '').strip()
        if not content: return Response({'error': 'Empty message'}, status=status.HTTP_400_BAD_REQUEST)
        if len(content) > 5000: return Response({'error': 'Message too long'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            conv = Conversation.objects.get(id=conv_id, workspace__memberships__user=request.user)
        except Conversation.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        client_msg_id = request.data.get('client_message_id')
        if not client_msg_id: return Response({'error': 'Missing client_message_id'}, status=status.HTTP_400_BAD_REQUEST)
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)
        msg = Message.objects.create(conversation=conv, sender=request.user, sender_type=Message.SenderType.USER, content=content, client_message_id=client_msg_id)
        from sla.services import mark_first_response, clear_next_response
        mark_first_response(conv)
        clear_next_response(conv)
        from automations.events import publish_event
        publish_event(
            'OPERATOR_MESSAGE_CREATED', conv.workspace_id, conversation_id=conv.id,
            actor_id=request.user.id, actor_type='USER', payload={'content': msg.content, 'message_type': msg.message_type, 'sender_type': 'USER'},
        )
        data = MessageSerializer(msg, context={'request': request}).data
        _broadcast(conv_id, data)
        return Response(data, status=201)

# --- WIDGET (VISITOR-FACING) RICH MESSAGE VIEWS ---
# These mirror the operator-side endpoints above but authenticate via the
# visitor's session_token instead of a JWT, matching StartCustomerChatView.

def _get_visitor_conversation(session_token, conv_id):
    session = VisitorSession.objects.get(token=session_token)
    return Conversation.objects.get(id=conv_id, visitor=session.visitor, type=Conversation.Type.CUSTOMER)

class WidgetMessageListView(APIView):
    permission_classes = []
    def get(self, request, conv_id):
        try:
            conv = _get_visitor_conversation(request.query_params.get('session_token'), conv_id)
        except (VisitorSession.DoesNotExist, Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Internal notes must never reach the visitor-facing widget.
        msgs = conv.messages.exclude(message_type=Message.MessageType.INTERNAL_NOTE).order_by('created_at')
        return Response(MessageSerializer(msgs, many=True, context={'request': request}).data)

class WidgetBrandingView(APIView):
    """Lets the widget re-fetch store/consultant branding on demand (e.g.
    after a reconnect where a live `branding.updated` WS event may have been
    missed while disconnected).
    """
    permission_classes = []
    def get(self, request, conv_id):
        try:
            conv = _get_visitor_conversation(request.query_params.get('session_token'), conv_id)
        except (VisitorSession.DoesNotExist, Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_widget_branding(conv))

class WidgetMarkReadView(APIView):
    permission_classes = []
    def post(self, request, conv_id):
        try:
            conv = _get_visitor_conversation(request.data.get('session_token'), conv_id)
        except (VisitorSession.DoesNotExist, Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        for msg in conv.messages.exclude(receipts__visitor=conv.visitor).exclude(sender_type=Message.SenderType.VISITOR):
            MessageReceipt.objects.create(message=msg, visitor=conv.visitor)
        _broadcast_seen(conv.id, 'VISITOR')
        return Response(status=200)

class WidgetUploadView(APIView):
    permission_classes = []
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'media_upload'
    def post(self, request, conv_id):
        try:
            conv = _get_visitor_conversation(request.data.get('session_token'), conv_id)
        except (VisitorSession.DoesNotExist, Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        message_type = request.data.get('message_type', Message.MessageType.IMAGE)
        if message_type not in (Message.MessageType.IMAGE, Message.MessageType.VOICE):
            return Response({'error': 'Invalid message_type'}, status=status.HTTP_400_BAD_REQUEST)
        file = request.FILES.get('file')
        if not file:
            return Response({'error': 'Missing file'}, status=status.HTTP_400_BAD_REQUEST)
        client_msg_id = request.data.get('client_message_id')
        if not client_msg_id:
            return Response({'error': 'Missing client_message_id'}, status=status.HTTP_400_BAD_REQUEST)
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)
        try:
            file = validate_and_normalize_upload(file, message_type)
        except UploadValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        metadata = {}
        caption = (request.data.get('caption') or '').strip()
        if caption:
            metadata['caption'] = caption
        duration = request.data.get('duration')
        if duration:
            metadata['duration'] = duration
        msg = Message.objects.create(
            conversation=conv, sender_type=Message.SenderType.VISITOR, sender_visitor=conv.visitor,
            content='', client_message_id=client_msg_id, message_type=message_type,
            metadata=metadata, attachment=file,
        )
        data = MessageSerializer(msg, context={'request': request}).data
        _broadcast(conv.id, data)
        return Response(data, status=201)

class WidgetRateConversationView(APIView):
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'widget_rating'
    def post(self, request, conv_id):
        try:
            conv = _get_visitor_conversation(request.data.get('session_token'), conv_id)
        except (VisitorSession.DoesNotExist, Conversation.DoesNotExist, DjangoValidationError, ValueError):
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            rating = int(request.data.get('rating'))
        except (TypeError, ValueError):
            return Response({'error': 'Invalid rating'}, status=status.HTTP_400_BAD_REQUEST)
        if rating < 1 or rating > 5:
            return Response({'error': 'Rating must be between 1 and 5'}, status=status.HTTP_400_BAD_REQUEST)
        if conv.rating is not None:
            return Response(
                {'error': 'Conversation has already been rated'},
                status=status.HTTP_409_CONFLICT,
            )
        client_msg_id = request.data.get('client_message_id') or f'rating_{conv.id}'
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate message'}, status=status.HTTP_409_CONFLICT)
        msg = Message.objects.create(
            conversation=conv, sender_type=Message.SenderType.VISITOR, sender_visitor=conv.visitor,
            content='', client_message_id=client_msg_id, message_type=Message.MessageType.RATING,
            metadata={'rating': rating},
        )
        _broadcast(conv.id, MessageSerializer(msg, context={'request': request}).data)
        conv.rating = rating
        conv.save(update_fields=['rating'])
        from automations.events import publish_event
        publish_event('RATING_SUBMITTED', conv.workspace_id, conversation_id=conv.id, actor_type='VISITOR', payload={'rating': rating})
        return Response(ConversationSerializer(conv).data, status=200)

# --- PLATFORM SUPPORT VIEWS ---

class WorkspaceSupportViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer
    permission_classes = [IsWorkspaceAdmin]

    def get_queryset(self):
        return Conversation.objects.filter(workspace__memberships__user=self.request.user, type=Conversation.Type.PLATFORM_SUPPORT)

    def create(self, request, *args, **kwargs):
        ws = request.user.workspace_memberships.filter(role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']).first()
        if not ws: return Response({'error': 'Not admin'}, status=403)
        conv = Conversation.objects.create(
            workspace=ws.workspace, type=Conversation.Type.PLATFORM_SUPPORT, 
            status=Conversation.Status.WAITING_FOR_PLATFORM, subject=request.data.get('subject', 'Support')
        )
        AuditEvent.objects.create(actor=request.user, action='support_conversation_created', target_type='conversation', target_id=str(conv.id))
        return Response(ConversationSerializer(conv).data, status=201)

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        conv = self.get_object()
        msgs = conv.messages.all().order_by('created_at')
        return Response(MessageSerializer(msgs, many=True).data)

    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        conv = self.get_object()
        content = request.data.get('content', '').strip()
        if not content: return Response({'error': 'Empty'}, status=400)
        if len(content) > 5000: return Response({'error': 'Too long'}, status=400)
        
        client_msg_id = request.data.get('client_message_id')
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate'}, status=409)
            
        msg = Message.objects.create(conversation=conv, sender=request.user, sender_type=Message.SenderType.USER, content=content, client_message_id=client_msg_id)
        conv.status = Conversation.Status.WAITING_FOR_PLATFORM
        conv.save()
        
        async_to_sync(get_channel_layer().group_send)(f"support_chat_{conv.id}", {'type': 'chat.message', 'message': {'id': str(msg.id), 'sender_type': 'USER', 'content': msg.content, 'created_at': msg.created_at.isoformat()}})
        return Response(MessageSerializer(msg).data, status=201)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        conv = self.get_object()
        for msg in conv.messages.exclude(receipts__user=request.user):
            MessageReceipt.objects.create(message=msg, user=request.user)
        return Response(status=200)

class PlatformSupportViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Platform-side inbox for store-admin support conversations.

    Deliberately NOT a ModelViewSet: there is no generic create/update/delete
    (a support agent must never be able to edit or delete a store's
    conversation). Platform-initiated conversations get their own explicit,
    separately-authorized endpoint. Scoped to platforms the caller belongs to.
    """
    serializer_class = ConversationSerializer
    permission_classes = [IsPlatformSupportAgent]

    def get_queryset(self):
        return Conversation.objects.filter(
            workspace__platform__memberships__user=self.request.user, type=Conversation.Type.PLATFORM_SUPPORT,
        ).distinct().order_by('-updated_at')

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        conv = self.get_object()
        msgs = conv.messages.all().order_by('created_at')
        return Response(MessageSerializer(msgs, many=True).data)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        conv = self.get_object()
        for msg in conv.messages.exclude(receipts__user=request.user):
            MessageReceipt.objects.create(message=msg, user=request.user)
        return Response(status=200)

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        conv = self.get_object()
        # Defence in depth: get_object() is already platform-scoped.
        if not request.user.platform_memberships.filter(platform=conv.workspace.platform).exists():
            return Response({'error': 'Cross-platform assignment denied'}, status=403)

        previous = conv.assigned_to
        conv.assigned_to = request.user
        conv.save(update_fields=['assigned_to', 'updated_at'])
        Assignment.objects.create(
            conversation=conv, assigned_to=request.user, assigned_by=request.user,
            previous_assignee=previous, action=Assignment.Action.REASSIGN if previous else Assignment.Action.CLAIM,
        )
        AuditEvent.objects.create(actor=request.user, action='support_conversation_assigned', target_type='conversation', target_id=str(conv.id))
        return Response(ConversationSerializer(conv, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def reply(self, request, pk=None):
        conv = self.get_object()
        content = (request.data.get('content') or '').strip()
        if not content: return Response({'error': 'Empty'}, status=400)
        if len(content) > 5000: return Response({'error': 'Too long'}, status=400)
        client_msg_id = request.data.get('client_message_id')
        if not client_msg_id: return Response({'error': 'Missing client_message_id'}, status=400)
        if Message.objects.filter(conversation=conv, client_message_id=client_msg_id).exists():
            return Response({'error': 'Duplicate'}, status=409)

        msg = Message.objects.create(conversation=conv, sender=request.user, sender_type=Message.SenderType.USER, content=content, client_message_id=client_msg_id)
        conv.status = Conversation.Status.WAITING_FOR_WORKSPACE
        conv.save()

        async_to_sync(get_channel_layer().group_send)(f"support_chat_{conv.id}", {'type': 'chat.message', 'message': {'id': str(msg.id), 'sender_type': 'USER', 'content': msg.content, 'created_at': msg.created_at.isoformat()}})
        return Response(MessageSerializer(msg).data, status=201)


class OperationalSummaryView(APIView):
    """Live operational snapshot for the supervisor dashboard: unassigned
    work, per-queue/per-team/per-agent load, urgent/waiting counts, and SLA
    health. Operational queries only — no analytics warehouse, no long-range
    aggregation beyond the requested `hours` window for average first response.
    """
    # Coarse gate only — the real, workspace-specific authorization happens
    # below, after the target workspace is resolved. A global "is this user
    # a supervisor/admin of *some* workspace" permission class was the
    # audited vulnerability: it let a supervisor of Workspace A view
    # Workspace B's summary merely by holding any membership in B.
    permission_classes = [drf_permissions.IsAuthenticated]

    def get(self, request):
        from accounts.models import OperatorPresence
        from teams.models import Team
        from queues.models import Queue

        workspace = resolve_operator_workspace(request.user, request.query_params.get('workspace_id'))
        if not user_can_supervise_workspace(request.user, workspace):
            raise PermissionDenied('Not authorized to view this workspace\'s operational summary')
        try:
            hours = int(request.query_params.get('hours', 24))
        except ValueError:
            hours = 24
        since = timezone.now() - timezone.timedelta(hours=hours)

        base = Conversation.objects.filter(workspace=workspace, type=Conversation.Type.CUSTOMER)
        active = base.filter(status__in=Conversation.ACTIVE_STATUSES)

        unassigned_count = active.filter(assigned_to__isnull=True).count()
        urgent_count = active.filter(priority=Conversation.Priority.URGENT).count()
        approaching_sla_count = active.filter(
            models.Q(sla__first_response_approaching_notified_at__isnull=False, sla__first_response_breached_at__isnull=True)
            | models.Q(sla__next_response_approaching_notified_at__isnull=False, sla__next_response_breached_at__isnull=True)
            | models.Q(sla__resolution_approaching_notified_at__isnull=False, sla__resolution_breached_at__isnull=True)
        ).distinct().count()
        breached_sla_count = active.filter(
            models.Q(sla__first_response_breached_at__isnull=False)
            | models.Q(sla__next_response_breached_at__isnull=False)
            | models.Q(sla__resolution_breached_at__isnull=False)
        ).distinct().count()

        # Aggregated in one query per breakdown rather than one .count() per
        # row — see docs/runbooks/TEAM_OPERATIONS_LOCAL_RUN.md known-issues
        # history; this used to be an N+1 (one query per queue/team, plus a
        # get_or_create + count per agent).
        active_conv_filter = models.Q(
            conversations__type=Conversation.Type.CUSTOMER, conversations__status__in=Conversation.ACTIVE_STATUSES,
        )
        by_queue = [
            {'queue_id': str(row['id']), 'name': row['name'], 'active_count': row['active_count']}
            for row in Queue.objects.filter(workspace=workspace, is_active=True)
            .annotate(active_count=models.Count('conversations', filter=active_conv_filter, distinct=True))
            .order_by('routing_priority', 'name').values('id', 'name', 'active_count')
        ]
        by_team = [
            {'team_id': str(row['id']), 'name': row['name'], 'active_count': row['active_count']}
            for row in Team.objects.filter(workspace=workspace, is_active=True)
            .annotate(active_count=models.Count('conversations', filter=active_conv_filter, distinct=True))
            .order_by('name').values('id', 'name', 'active_count')
        ]

        memberships = list(workspace.memberships.select_related('user').all())
        user_ids = [m.user_id for m in memberships]
        presences = {p.user_id: p for p in OperatorPresence.objects.filter(user_id__in=user_ids)}
        # Mirrors OperatorPresence.active_conversation_count() exactly (all
        # conversation types, not just CUSTOMER — that method has no type
        # filter), just computed for every agent in one query instead of one
        # query per agent. No row is created here for an agent with no
        # presence yet — a read-only summary should not have that side
        # effect; a sensible in-memory default stands in for the response.
        active_counts = dict(
            Conversation.objects.filter(assigned_to_id__in=user_ids)
            .exclude(status__in=[Conversation.Status.CLOSED, Conversation.Status.RESOLVED])
            .values('assigned_to_id').annotate(c=models.Count('id')).values_list('assigned_to_id', 'c')
        )
        agents = []
        for membership in memberships:
            user = membership.user
            presence = presences.get(user.id)
            active_count = active_counts.get(user.id, 0)
            max_capacity = presence.max_capacity if presence else OperatorPresence.DEFAULT_MAX_CAPACITY
            status_ = presence.effective_status() if presence else OperatorPresence.Status.OFFLINE
            agents.append({
                'user_id': str(user.id),
                'display_name': user.display_name or user.email.split('@')[0],
                'status': status_,
                'active_conversation_count': active_count,
                'max_capacity': max_capacity,
                'at_capacity': active_count >= max_capacity,
            })

        responded = base.filter(sla__first_responded_at__gte=since, sla__isnull=False)
        response_minutes = [
            (c.sla.first_responded_at - c.created_at).total_seconds() / 60
            for c in responded.select_related('sla') if c.sla.first_responded_at
        ]
        avg_first_response_minutes = round(sum(response_minutes) / len(response_minutes), 1) if response_minutes else None

        return Response({
            'workspace_id': str(workspace.id),
            'unassigned_count': unassigned_count,
            'waiting_count': unassigned_count,
            'urgent_count': urgent_count,
            'approaching_sla_count': approaching_sla_count,
            'breached_sla_count': breached_sla_count,
            'by_queue': by_queue,
            'by_team': by_team,
            'by_agent': agents,
            'agents_at_capacity': sum(1 for a in agents if a['at_capacity']),
            'avg_first_response_minutes': avg_first_response_minutes,
            'period_hours': hours,
        })
