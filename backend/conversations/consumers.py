import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.conf import settings
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model
from .models import Conversation, Message, MessageReceipt
from .branding import build_widget_branding
from visitors.sessions import get_valid_session
from workspaces.models import WorkspaceMembership
from platforms.models import PlatformMembership
from accounts.presence import touch_presence
from common.ws_throttling import is_rate_limited
from common.ws_auth import RevalidatingConsumerMixin

User = get_user_model()

class BaseChatConsumer(RevalidatingConsumerMixin, AsyncJsonWebsocketConsumer):
    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        if text_data:
            try: json.loads(text_data)
            except json.JSONDecodeError: return
        await super().receive(text_data, bytes_data, **kwargs)

    async def chat_message(self, event):
        await self.send_json({**event['message'], 'type': 'chat.message'})

    async def typing_indicator(self, event):
        # Don't echo the typing event back to the person who is typing.
        if event.get('origin_channel') == self.channel_name:
            return
        await self.send_json({'type': 'typing', 'sender_type': event['sender_type']})

    async def message_seen(self, event):
        await self.send_json({'type': 'message.seen', 'reader': event['reader']})

    async def branding_updated(self, event):
        # Delivered to both widget and dashboard sockets in the group; only the
        # widget acts on it (a live consultant-identity/presence refresh), but
        # both consumer types need a handler or Channels errors on dispatch.
        await self.send_json({'type': 'branding.updated', 'branding': event['branding']})


class OpsEventsMixin:
    """Operator-only realtime events, delivered on the `chat_ops_<id>` group
    that only DashboardChatConsumer ever joins — never the widget socket, so
    none of these can leak to a customer. Each handler just forwards the
    event dict as-is: it already carries the original dotted `type` plus
    whatever JSON-safe payload the sender included, giving every event a
    stable envelope of `{type, ...}`.
    """
    async def conversation_queued(self, event):
        await self.send_json(event)

    async def conversation_assigned(self, event):
        await self.send_json(event)

    async def conversation_unassigned(self, event):
        await self.send_json(event)

    async def conversation_transferred(self, event):
        await self.send_json(event)

    async def conversation_escalated(self, event):
        await self.send_json(event)

    async def conversation_priority_updated(self, event):
        await self.send_json(event)

    async def conversation_sla_updated(self, event):
        await self.send_json(event)

    async def conversation_sla_approaching(self, event):
        await self.send_json(event)

    async def conversation_sla_breached(self, event):
        await self.send_json(event)

    async def conversation_internal_note_created(self, event):
        await self.send_json(event)

class WidgetChatConsumer(BaseChatConsumer):
    async def connect(self):
        self.session_token = self.scope['url_route']['kwargs']['session_token']
        self.conv_id = self.scope['url_route']['kwargs']['conv_id']
        self.conversation = await self._get_visitor_conversation()
        if not self.conversation: await self.close(); return
        self.group_name = f"chat_{self.conv_id}"
        await self.accept()
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        self.authz_enabled = True

    @database_sync_to_async
    def _get_visitor_conversation(self):
        try:
            session = get_valid_session(self.session_token)
            if session is None:
                return None
            project = session.visitor.project
            if not (project.is_active and project.workspace.is_active):
                return None
            return Conversation.objects.get(id=self.conv_id, visitor=session.visitor, type=Conversation.Type.CUSTOMER)
        except Exception: return None

    async def is_still_authorized(self):
        return await self._get_visitor_conversation() is not None

    async def leave_groups(self):
        if hasattr(self, 'group_name'): await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def disconnect(self, close_code):
        await self.leave_groups()

    async def receive_json(self, content):
        msg_kind = content.get('type')
        if msg_kind == 'typing':
            await self.channel_layer.group_send(self.group_name, {'type': 'typing.indicator', 'sender_type': 'VISITOR', 'origin_channel': self.channel_name})
            return
        if msg_kind == 'mark_read':
            await self._mark_read()
            await self.channel_layer.group_send(self.group_name, {'type': 'message.seen', 'reader': 'VISITOR'})
            return
        msg_text = content.get('message', '').strip()
        if not msg_text or len(msg_text) > 5000: return
        # DRF's `widget_message`/`widget_start` throttle scopes only ever
        # run on the REST cycle — a client that skips those endpoints and
        # sends straight over this socket would otherwise be unbounded.
        # Keyed on the visitor session token (established once at connect(),
        # cannot be forged mid-socket) rather than IP, so it isn't defeated
        # by shared/rotating IPs behind NAT or a reverse proxy.
        limited = await is_rate_limited(
            'widget_ws_message', self.session_token,
            settings.WIDGET_WS_MESSAGE_RATE_LIMIT, settings.WIDGET_WS_MESSAGE_RATE_WINDOW_SECONDS,
        )
        if limited:
            await self.send_json({'type': 'rate_limited', 'retry_after': settings.WIDGET_WS_MESSAGE_RATE_WINDOW_SECONDS})
            return
        msg = await self._save_visitor_message(content.get('client_message_id'), msg_text)
        if not msg: return
        await self.channel_layer.group_send(self.group_name, {'type': 'chat.message', 'message': {
            'id': str(msg.id), 'sender_type': 'VISITOR', 'content': msg.content,
            'message_type': msg.message_type, 'metadata': msg.metadata, 'attachment_url': None,
            'client_message_id': msg.client_message_id, 'created_at': msg.created_at.isoformat(),
        }})

    @database_sync_to_async
    def _save_visitor_message(self, client_msg_id, msg_text):
        if Message.objects.filter(conversation=self.conversation, client_message_id=client_msg_id).exists(): return None
        msg = Message.objects.create(conversation=self.conversation, sender_type=Message.SenderType.VISITOR, sender_visitor=self.conversation.visitor, content=msg_text, client_message_id=client_msg_id)
        from sla.services import recalculate_next_response
        recalculate_next_response(self.conversation)
        from automations.events import publish_event
        publish_event(
            'CUSTOMER_MESSAGE_CREATED', self.conversation.workspace_id, conversation_id=self.conversation.id,
            actor_type='VISITOR', payload={'content': msg.content, 'message_type': msg.message_type, 'sender_type': 'VISITOR'},
        )
        return msg

    @database_sync_to_async
    def _mark_read(self):
        for msg in self.conversation.messages.exclude(receipts__visitor=self.conversation.visitor).exclude(sender_type=Message.SenderType.VISITOR):
            MessageReceipt.objects.create(message=msg, visitor=self.conversation.visitor)

class DashboardChatConsumer(OpsEventsMixin, BaseChatConsumer):
    async def connect(self):
        self.token = self.scope['url_route']['kwargs']['token']
        self.conv_id = self.scope['url_route']['kwargs']['conv_id']
        self.conversation = await self._get_user_conversation()
        if not self.conversation: await self.close(); return
        self.group_name = f"chat_{self.conv_id}"
        self.ops_group_name = f"chat_ops_{self.conv_id}"
        await self.accept()
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.channel_layer.group_add(self.ops_group_name, self.channel_name)
        self.authz_enabled = True
        is_assigned_operator = await self._touch_presence_and_check_assigned()
        if is_assigned_operator:
            branding = await self._build_branding()
            await self.channel_layer.group_send(self.group_name, {'type': 'branding.updated', 'branding': branding})

    @database_sync_to_async
    def _get_user_conversation(self):
        try:
            access_token = AccessToken(self.token)
            user = User.objects.get(id=access_token['user_id'], is_active=True)
            conv = self._conversation_for(user)
            if conv:
                self.user = user
            return conv
        except Exception: return None

    def _conversation_for(self, user):
        try:
            return Conversation.objects.get(
                id=self.conv_id, workspace__memberships__user=user, workspace__is_active=True,
                type=Conversation.Type.CUSTOMER,
            )
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def _authorized_for_user(self):
        # Deliberately re-reads the user row (is_active) and the membership but
        # NOT the JWT: the token's own exp was enforced at connect time; a
        # long-lived socket must survive token expiry, not permission changes.
        user = User.objects.filter(id=self.user.id, is_active=True).first()
        return bool(user and self._conversation_for(user))

    async def is_still_authorized(self):
        return await self._authorized_for_user()

    @database_sync_to_async
    def _touch_presence_and_check_assigned(self):
        touch_presence(self.user)
        return self.conversation.assigned_to_id == self.user.id

    @database_sync_to_async
    def _build_branding(self):
        return build_widget_branding(self.conversation)

    async def leave_groups(self):
        if hasattr(self, 'group_name'): await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if hasattr(self, 'ops_group_name'): await self.channel_layer.group_discard(self.ops_group_name, self.channel_name)

    async def disconnect(self, close_code):
        await self.leave_groups()

    async def receive_json(self, content):
        msg_kind = content.get('type')
        if msg_kind == 'typing':
            await self.channel_layer.group_send(self.group_name, {'type': 'typing.indicator', 'sender_type': 'USER', 'origin_channel': self.channel_name})
            return
        if msg_kind == 'mark_read':
            await self._mark_read()
            await self.channel_layer.group_send(self.group_name, {'type': 'message.seen', 'reader': 'USER'})
            return
        msg_text = content.get('message', '').strip()
        if not msg_text or len(msg_text) > 5000: return
        msg = await self._save_user_message(content.get('client_message_id'), msg_text)
        if not msg: return
        await self.channel_layer.group_send(self.group_name, {'type': 'chat.message', 'message': {
            'id': str(msg.id), 'sender_type': 'USER', 'content': msg.content,
            'message_type': msg.message_type, 'metadata': msg.metadata, 'attachment_url': None,
            'client_message_id': msg.client_message_id, 'created_at': msg.created_at.isoformat(),
        }})

    @database_sync_to_async
    def _save_user_message(self, client_msg_id, msg_text):
        if Message.objects.filter(conversation=self.conversation, client_message_id=client_msg_id).exists(): return None
        touch_presence(self.user)
        msg = Message.objects.create(conversation=self.conversation, sender_type=Message.SenderType.USER, sender=self.user, content=msg_text, client_message_id=client_msg_id)
        from sla.services import mark_first_response, clear_next_response
        mark_first_response(self.conversation)
        clear_next_response(self.conversation)
        from automations.events import publish_event
        publish_event(
            'OPERATOR_MESSAGE_CREATED', self.conversation.workspace_id, conversation_id=self.conversation.id,
            actor_id=self.user.id, actor_type='USER', payload={'content': msg.content, 'message_type': msg.message_type, 'sender_type': 'USER'},
        )
        return msg

    @database_sync_to_async
    def _mark_read(self):
        touch_presence(self.user)
        for msg in self.conversation.messages.exclude(receipts__user=self.user).exclude(sender_type=Message.SenderType.USER):
            MessageReceipt.objects.create(message=msg, user=self.user)

class DashboardSupportConsumer(BaseChatConsumer):
    async def connect(self):
        self.token = self.scope['url_route']['kwargs']['token']
        self.conv_id = self.scope['url_route']['kwargs']['conv_id']
        self.conversation = await self._get_support_conversation()
        if not self.conversation: await self.close(); return
        self.group_name = f"support_chat_{self.conv_id}"
        await self.accept()
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        self.authz_enabled = True

    def _support_conversation_for(self, user):
        """The support conversation iff `user` is Owner/Admin of THIS workspace
        or platform staff of THIS workspace's platform (and both are active)."""
        conv = Conversation.objects.select_related('workspace__platform').filter(
            id=self.conv_id, type=Conversation.Type.PLATFORM_SUPPORT,
        ).first()
        if not conv or not conv.workspace.is_active or not conv.workspace.platform.is_active:
            return None
        is_ws_admin = WorkspaceMembership.objects.filter(
            user=user, workspace=conv.workspace, role__in=['WORKSPACE_OWNER', 'WORKSPACE_ADMIN']).exists()
        is_pl_support = PlatformMembership.objects.filter(
            user=user, platform=conv.workspace.platform,
            role__in=['PLATFORM_OWNER', 'PLATFORM_ADMIN', 'PLATFORM_SUPPORT_AGENT']).exists()
        return conv if (is_ws_admin or is_pl_support) else None

    @database_sync_to_async
    def _get_support_conversation(self):
        try:
            access_token = AccessToken(self.token)
            user = User.objects.get(id=access_token['user_id'], is_active=True)
            conv = self._support_conversation_for(user)
            if conv:
                self.user = user
            return conv
        except Exception: return None

    @database_sync_to_async
    def _authorized_for_user(self):
        user = User.objects.filter(id=self.user.id, is_active=True).first()
        return bool(user and self._support_conversation_for(user))

    async def is_still_authorized(self):
        return await self._authorized_for_user()

    async def leave_groups(self):
        if hasattr(self, 'group_name'): await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def disconnect(self, close_code):
        await self.leave_groups()

    async def receive_json(self, content):
        msg_text = content.get('message', '').strip()
        if not msg_text or len(msg_text) > 5000: return
        msg = await self._save_support_message(content.get('client_message_id'), msg_text)
        if not msg: return
        await self.channel_layer.group_send(self.group_name, {'type': 'chat.message', 'message': {'id': str(msg.id), 'sender_type': 'USER', 'content': msg.content, 'created_at': msg.created_at.isoformat()}})

    @database_sync_to_async
    def _save_support_message(self, client_msg_id, msg_text):
        if Message.objects.filter(conversation=self.conversation, client_message_id=client_msg_id).exists(): return None
        return Message.objects.create(conversation=self.conversation, sender_type=Message.SenderType.USER, sender=self.user, content=msg_text, client_message_id=client_msg_id)
