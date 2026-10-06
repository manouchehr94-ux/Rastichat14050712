"""Small Redis helpers shared by the integration endpoints: single-use claims (replay protection) and counters.

Redis being unavailable FAILS CLOSED: a token whose single use cannot be recorded is refused rather than accepted.
"""
import redis as redis_lib
from django.conf import settings

_redis = None


def get_redis():
    global _redis
    if _redis is None:
        conn = settings.REDIS_CONNECTION
        _redis = redis_lib.Redis(
            host=conn['host'], port=conn['port'], password=conn['password'], db=conn['db'],
            socket_connect_timeout=2, socket_timeout=2,
        )
    return _redis


class StoreUnavailable(Exception):
    pass


def claim_once(key, ttl_seconds):
    """True the first time `key` is claimed within `ttl_seconds`, False on every later attempt."""
    try:
        return bool(get_redis().set(key, 1, nx=True, ex=max(1, int(ttl_seconds))))
    except redis_lib.RedisError as exc:
        raise StoreUnavailable(str(exc)) from exc


def hit(key, window_seconds):
    """Increment a fixed-window counter and return its new value."""
    try:
        pipe = get_redis().pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, int(window_seconds), nx=True)
        count, _ = pipe.execute()
        return int(count)
    except redis_lib.RedisError as exc:
        raise StoreUnavailable(str(exc)) from exc


def peek(key):
    try:
        return int(get_redis().get(key) or 0)
    except redis_lib.RedisError as exc:
        raise StoreUnavailable(str(exc)) from exc
