"""Structured events + counters (master spec §36: integration bootstrap/provisioning/SSO/replay/pre-chat/conversation/WS/cross-tenant/rate-limit).

`emit()` writes ONE greppable log line (`rastichat_event event=<name> k=v …`) and bumps a day-bucketed Redis counter that
the token-protected `/api/v1/health/monitoring/` endpoint reports. Both are best-effort: observability must never break a
request, and a Redis outage here is invisible to callers (the security-relevant Redis paths fail closed on their own).

What may appear in a field: short identifiers, codes, booleans, counts. NEVER credentials — field names that sound like one are
dropped, values are truncated and stripped of whitespace/control characters, so a careless caller cannot leak a token into logs.
"""
import logging
import re
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger('rastichat.events')

COUNTER_PREFIX = 'rc:metric'
COUNTER_TTL_SECONDS = 14 * 24 * 3600
_DROP_FIELDS = ('token', 'secret', 'assertion', 'authorization', 'password', 'credential', 'cookie', 'key_material')
_SAFE = re.compile(r'[^A-Za-z0-9_.:@/-]')


def _clean(value):
    return _SAFE.sub('_', str(value))[:64]


def _fields(raw):
    return {k: _clean(v) for k, v in raw.items() if not any(bad in k.lower() for bad in _DROP_FIELDS) and v is not None}


def emit(event, *, level=logging.INFO, label=None, **fields):
    """Log + count `event`. `label` (a short code such as a refusal reason) becomes part of the counter name."""
    try:
        clean = _fields(fields)
        if label is not None:
            clean['label'] = _clean(label)
        logger.log(level, 'rastichat_event event=%s %s', event, ' '.join(f'{k}={v}' for k, v in sorted(clean.items())))
        name = f'{event}:{clean["label"]}' if 'label' in clean else event
        _count(name)
    except Exception:  # noqa: BLE001 - never let observability break a request
        pass


def _count(name):
    from integrations.redis_store import get_redis
    key = f'{COUNTER_PREFIX}:{timezone.now():%Y%m%d}:{name}'
    pipe = get_redis().pipeline(transaction=False)
    pipe.incr(key)
    pipe.expire(key, COUNTER_TTL_SECONDS, nx=True)
    pipe.execute()


def counters(days=1):
    """{counter name: total over the last `days` UTC days} — for the monitoring endpoint."""
    from integrations.redis_store import get_redis
    redis = get_redis()
    totals = {}
    today = timezone.now()
    for offset in range(max(1, days)):
        day = f'{today - timedelta(days=offset):%Y%m%d}'
        for key in redis.scan_iter(match=f'{COUNTER_PREFIX}:{day}:*', count=500):
            name = key.decode().split(':', 3)[3]
            totals[name] = totals.get(name, 0) + int(redis.get(key) or 0)
    return dict(sorted(totals.items()))
