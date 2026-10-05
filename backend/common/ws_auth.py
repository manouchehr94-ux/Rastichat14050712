"""Live re-authorization for WebSocket consumers.

Connect-time checks alone leave a hole: a user deactivated, removed from a
workspace, or demoted after the socket opened keeps sending and receiving
until they happen to reconnect. `RevalidatingConsumerMixin` re-checks
authorization (via the consumer's own `is_still_authorized()`) before
*every* inbound client frame and *every* channel-layer event that would be
delivered to the client. On failure the socket is closed with code 4403, the
consumer leaves its groups, and the event/frame is dropped — nothing is sent
to or accepted from a revoked client.

The check is memoised for `settings.WS_REVALIDATE_SECONDS` (default 5s) so
a chatty room costs at most one authorization query per socket per window;
that window is the upper bound on how long a revoked client can still
receive. Set it to 0 to check every event (tests do).
"""
import asyncio
import json
import time

from django.conf import settings

from . import legacy_credentials
from .ws_tickets import consume_ticket

CLOSE_CODE_REVOKED = 4403


class RevalidatingConsumerMixin:
    authz_enabled = False  # flipped on by connect() once the socket is authorized
    _authz_ok_at = None
    _authz_revoked = False

    async def is_still_authorized(self) -> bool:  # pragma: no cover - overridden
        return True

    async def leave_groups(self):  # pragma: no cover - overridden
        pass

    async def dispatch(self, message):
        if self._authz_revoked:
            if message.get('type') == 'websocket.disconnect':
                await super().dispatch(message)
            return  # drop everything else for a revoked socket
        if self.authz_enabled and message.get('type') != 'websocket.disconnect':
            if not await self._authorized_now():
                await self._revoke()
                return
        await super().dispatch(message)

    async def _authorized_now(self) -> bool:
        interval = getattr(settings, 'WS_REVALIDATE_SECONDS', 5)
        now = time.monotonic()
        if interval and self._authz_ok_at is not None and now - self._authz_ok_at < interval:
            return True
        if await self.is_still_authorized():
            self._authz_ok_at = now
            return True
        return False

    async def _revoke(self):
        self._authz_revoked = True
        try:
            await self.leave_groups()
        finally:
            await self.close(code=CLOSE_CODE_REVOKED)


class TicketAuthMixin:
    """Authenticate a socket with a single-use ticket sent in its FIRST frame (no credential in
    the URL), while — only while `common.legacy_credentials.allowed(surface)` — still serving the
    legacy token-in-path routes.

    A ticket-mode socket is accepted but unauthenticated: it joins NO channel-layer group and
    receives nothing until a valid `{"type": "auth", "ticket": "..."}` frame has been consumed
    and the consumer's `bind_ticket_identity` has re-checked live authorization. Anything else
    (other first frame, bad/expired/replayed/mismatched ticket, silence past
    `WS_AUTH_TIMEOUT_SECONDS`) closes it with 4401.

    A consumer provides: TICKET_KIND, route_scope_id(), legacy_connect(), bind_ticket_identity(payload),
    join_groups() and optionally after_authenticated().
    """
    TICKET_KIND = None
    ticket_authenticated = False
    _auth_deadline_task = None

    LEGACY_ROUTE_KWARGS = ('token', 'session_token')
    LEGACY_SURFACE = legacy_credentials.SURFACE_DASHBOARD  # the widget consumer overrides this

    def is_ticket_mode(self):
        kwargs = self.scope['url_route']['kwargs']
        return not any(k in kwargs for k in self.LEGACY_ROUTE_KWARGS)

    def route_scope_id(self):
        return self.scope['url_route']['kwargs'].get('conv_id')

    async def connect(self):
        if self.is_ticket_mode():
            await self.accept()
            self._auth_deadline_task = asyncio.ensure_future(self._close_unless_authenticated())
            return
        if not legacy_credentials.allowed(self.LEGACY_SURFACE):
            await self.close(code=4401)
            return
        await self.legacy_connect()

    async def _close_unless_authenticated(self):
        await asyncio.sleep(settings.WS_AUTH_TIMEOUT_SECONDS)
        if not self.ticket_authenticated:
            await self.close(code=4401)

    def _cancel_deadline(self):
        task, self._auth_deadline_task = self._auth_deadline_task, None
        if task is not None and not task.done():
            task.cancel()

    async def receive_ticket_frame(self, text_data):
        try:
            data = json.loads(text_data) if text_data else None
        except ValueError:
            data = None
        ticket = data.get('ticket') if isinstance(data, dict) and data.get('type') == 'auth' else None
        if not isinstance(ticket, str):
            await self.close(code=4401)
            return
        payload = await consume_ticket(ticket, kind=self.TICKET_KIND, scope_id=self.route_scope_id())
        if payload is None:
            await self.close(code=4401)
            return
        if not await self.bind_ticket_identity(payload):
            await self.close(code=CLOSE_CODE_REVOKED)
            return
        self._cancel_deadline()
        await self.join_groups()
        self.ticket_authenticated = True
        self.authz_enabled = True
        await self.send_json({'type': 'auth.ok'})
        await self.after_authenticated()

    async def after_authenticated(self):
        pass
