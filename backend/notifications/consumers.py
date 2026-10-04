from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model

from common.ws_auth import RevalidatingConsumerMixin

User = get_user_model()


class NotificationsConsumer(RevalidatingConsumerMixin, AsyncJsonWebsocketConsumer):
    """One socket per logged-in operator: their personal notification feed,
    plus live presence updates for every workspace they belong to (used by
    the inbox/supervisor dashboard to show agents going online/away live).
    Purely a push channel — the client never sends anything meaningful here,
    so malformed/unexpected input is just ignored rather than erroring.
    """
    async def connect(self):
        self.token = self.scope['url_route']['kwargs']['token']
        user = await self._get_user()
        if not user:
            await self.close()
            return
        self.user = user
        self.group_name = f'notifications_{user.id}'
        self.workspace_groups = await self._workspace_groups(user)
        await self.accept()
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        for group in self.workspace_groups:
            await self.channel_layer.group_add(group, self.channel_name)
        self.authz_enabled = True

    @database_sync_to_async
    def _get_user(self):
        try:
            access_token = AccessToken(self.token)
            return User.objects.get(id=access_token['user_id'], is_active=True)
        except Exception:
            return None

    @database_sync_to_async
    def _workspace_groups(self, user):
        return [f'workspace_presence_{m.workspace_id}' for m in user.workspace_memberships.all()]

    @database_sync_to_async
    def _user_still_active(self):
        return User.objects.filter(id=self.user.id, is_active=True).exists()

    async def is_still_authorized(self):
        if not await self._user_still_active():
            return False
        # Keep workspace presence subscriptions in step with live memberships:
        # drop groups for workspaces the user was removed from, add new ones.
        current = set(await self._workspace_groups(self.user))
        for group in set(self.workspace_groups) - current:
            await self.channel_layer.group_discard(group, self.channel_name)
        for group in current - set(self.workspace_groups):
            await self.channel_layer.group_add(group, self.channel_name)
        self.workspace_groups = list(current)
        return True

    async def leave_groups(self):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        for group in getattr(self, 'workspace_groups', []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def disconnect(self, close_code):
        await self.leave_groups()

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        pass

    async def notification_created(self, event):
        await self.send_json(event)

    async def agent_presence_updated(self, event):
        # An event already queued for a workspace the user has just been
        # removed from must not be delivered even though the group is gone.
        workspace_id = event.get('workspace_id')
        if workspace_id is not None and f'workspace_presence_{workspace_id}' not in self.workspace_groups:
            return
        await self.send_json(event)
