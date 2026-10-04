"""Fixed-window rate limiting for WebSocket consumers.

DRF's throttle classes only ever run on the HTTP request/response cycle —
they have no hook into a Channels consumer's `receive_json`, so the REST
endpoint sitting next to a given WS action (e.g. `widget_start` on
`StartCustomerChatView`) throttling alone does nothing to stop a client that
skips the REST call and hammers the socket directly. This module is the
WebSocket-side equivalent: a small Redis INCR+EXPIRE fixed-window counter,
reusing the exact same Redis instance/credentials as the Channels layer
itself (settings.REDIS_CONNECTION).
"""
import time

import redis as redis_lib
from asgiref.sync import sync_to_async
from django.conf import settings

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        _redis = redis_lib.Redis(
            host=settings.REDIS_CONNECTION['host'],
            port=settings.REDIS_CONNECTION['port'],
            password=settings.REDIS_CONNECTION['password'],
            db=settings.REDIS_CONNECTION['db'],
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    return _redis


def _is_rate_limited_sync(scope: str, identity: str, limit: int, window_seconds: int) -> bool:
    window = int(time.time() // window_seconds)
    key = f'wsrate:{scope}:{identity}:{window}'
    try:
        r = _get_redis()
        count = r.incr(key)
        if count == 1:
            r.expire(key, window_seconds)
        return count > limit
    except Exception:
        # Redis is also what the Channels layer itself depends on to
        # deliver any message at all — if it's unreachable, the socket is
        # already non-functional for reasons this helper can't make worse.
        # Failing open here just means "don't add a second failure mode".
        return False


async def is_rate_limited(scope: str, identity: str, limit: int, window_seconds: int) -> bool:
    return await sync_to_async(_is_rate_limited_sync, thread_sensitive=False)(scope, identity, limit, window_seconds)
