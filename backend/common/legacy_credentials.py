"""Policy and telemetry for the legacy credentials-in-URL mechanism (see
docs/runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md).

* Outside staging/production the switch is the plain `LEGACY_URL_CREDENTIALS_ENABLED` (local tooling).
* On staging/production it is only ever honoured for the `widget` surface (visitor session tokens), only
  while the owner-approved window (`LEGACY_URL_CREDENTIALS_UNTIL`, UTC date, inclusive) has not passed —
  evaluated on every call, so a long-running process stops accepting legacy credentials by itself.
* Every accepted legacy credential is counted (per day / surface / project) in Redis so the owner can see
  which projects still run an old widget bundle before the window ends (`report_legacy_credential_usage`).
"""
import datetime
import logging

from asgiref.sync import sync_to_async
from django.conf import settings

import redis as redis_lib

logger = logging.getLogger(__name__)

SURFACE_WIDGET = 'widget'
SURFACE_DASHBOARD = 'dashboard'
USAGE_KEY_PREFIX = 'legacy_cred:'
USAGE_RETENTION_SECONDS = 60 * 24 * 3600

_redis = None


def utc_today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def allowed(surface):
    if not settings.LEGACY_URL_CREDENTIALS_ENABLED:
        return False
    if not settings.IS_PRODUCTION_LIKE:
        return True
    if surface != SURFACE_WIDGET:
        return False
    until = settings.LEGACY_URL_CREDENTIALS_UNTIL
    return until is not None and utc_today() <= until


def _get_redis():
    global _redis
    if _redis is None:
        conn = settings.REDIS_CONNECTION
        _redis = redis_lib.Redis(
            host=conn['host'], port=conn['port'], password=conn['password'], db=conn['db'],
            socket_connect_timeout=1, socket_timeout=1,
        )
    return _redis


def usage_key(day):
    return f'{USAGE_KEY_PREFIX}{day:%Y%m%d}'


def record_use(surface, project_id=None):
    """Best effort (never blocks or fails a connection): count one accepted legacy credential."""
    try:
        r = _get_redis()
        key = usage_key(utc_today())
        pipe = r.pipeline()
        pipe.hincrby(key, f'{surface}:{project_id or "-"}', 1)
        pipe.expire(key, USAGE_RETENTION_SECONDS)
        pipe.execute()
    except Exception:  # noqa: BLE001 - telemetry must never affect authentication
        logger.warning('could not record legacy credential usage', exc_info=True)


record_use_async = sync_to_async(record_use, thread_sensitive=False)


def read_usage(days):
    """{date: {(surface, project_id): count}} for the last `days` days (today included)."""
    r = _get_redis()
    out = {}
    today = utc_today()
    for offset in range(days):
        day = today - datetime.timedelta(days=offset)
        raw = r.hgetall(usage_key(day))
        if not raw:
            continue
        rows = {}
        for field, count in raw.items():
            surface, _, project = field.decode().partition(':')
            rows[(surface, project)] = int(count)
        out[day] = rows
    return out
