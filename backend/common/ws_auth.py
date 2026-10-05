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
import time

from django.conf import settings

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
