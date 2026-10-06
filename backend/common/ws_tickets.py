"""Short-lived, single-use WebSocket tickets.

A browser cannot set an `Authorization` header on a WebSocket handshake, which is
why long-lived credentials (a 60-minute JWT, a visitor session token) used to
ride in the URL path — and therefore into reverse-proxy access logs, browser
history and Referer headers. Instead, an authenticated REST call mints a ticket
that is

* **random** (256 bits) and stored only as a SHA-256 hash in Redis,
* **short-lived** (`WS_TICKET_TTL_SECONDS`, default 30s),
* **single-use** — consumed atomically (GET+DEL in one MULTI/EXEC transaction),
  so of two concurrent connection attempts at most one can win,
* **bound** to a connection kind (dashboard chat / support / notifications /
  widget), to the subject (user id or visitor-session id) and to the exact
  conversation it was issued for; a ticket replayed against another
  conversation or kind is rejected (and burned).

The ticket travels in the first WebSocket frame, never in the URL.
Redis being unavailable fails closed: no ticket can be issued or consumed.
"""
import hashlib
import json
import secrets

from asgiref.sync import sync_to_async
from django.conf import settings

import redis as redis_lib

from common import observability

KIND_DASHBOARD_CHAT = 'dashboard_chat'
KIND_SUPPORT = 'support'
KIND_NOTIFICATIONS = 'notifications'
KIND_WIDGET = 'widget'
KINDS = (KIND_DASHBOARD_CHAT, KIND_SUPPORT, KIND_NOTIFICATIONS, KIND_WIDGET)

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        conn = settings.REDIS_CONNECTION
        _redis = redis_lib.Redis(
            host=conn['host'], port=conn['port'], password=conn['password'], db=conn['db'],
            socket_connect_timeout=2, socket_timeout=2,
        )
    return _redis


def _key(ticket):
    return 'wsticket:' + hashlib.sha256(ticket.encode()).hexdigest()


def issue_ticket(*, kind, subject, scope_id=None):
    """Mint a ticket for `subject` (user id / visitor-session id) on `kind`, optionally
    bound to one conversation. Returns the raw ticket string (shown once)."""
    if kind not in KINDS:
        raise ValueError(f'unknown ticket kind {kind!r}')
    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({'kind': kind, 'sub': str(subject), 'scope': str(scope_id) if scope_id else None})
    _get_redis().set(_key(ticket), payload, ex=settings.WS_TICKET_TTL_SECONDS, nx=True)
    return ticket


def consume_ticket_sync(ticket, *, kind, scope_id=None):
    """Atomically take the ticket. Returns its payload iff it existed (unexpired,
    unused) AND matches `kind` and `scope_id`; otherwise None. The ticket is destroyed
    in every case in which it existed, so a mismatching replay burns it."""
    payload = _consume(ticket, kind=kind, scope_id=scope_id)
    observability.emit('ws_auth', label='ok' if payload else 'refused', kind=kind)
    return payload


def _consume(ticket, *, kind, scope_id=None):
    if not ticket or not isinstance(ticket, str) or len(ticket) > 200:
        return None
    pipe = _get_redis().pipeline(transaction=True)
    key = _key(ticket)
    pipe.get(key)
    pipe.delete(key)
    raw, _deleted = pipe.execute()
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if payload.get('kind') != kind:
        return None
    if (payload.get('scope') or None) != (str(scope_id) if scope_id else None):
        return None
    return payload


consume_ticket = sync_to_async(consume_ticket_sync, thread_sensitive=False)
