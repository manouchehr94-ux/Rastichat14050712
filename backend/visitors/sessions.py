"""Visitor-session lifecycle: validation, sliding renewal, revocation, rotation.

Every place that turns a `session_token` into a Visitor (REST widget views,
the widget WebSocket, public KB) goes through `get_valid_session` so an
expired, revoked, rotated-away or deactivated-project session can never reach
a customer's history over any channel.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import VisitorSession

SESSION_HEADER = 'X-Widget-Session'


def _ttl():
    return timedelta(days=settings.VISITOR_SESSION_TTL_DAYS)


def _max_age():
    return timedelta(days=settings.VISITOR_SESSION_MAX_AGE_DAYS)


def hard_limit(session):
    """The absolute end of the session's life (`hard_expires_at`, or created_at +
    max age for a row that somehow has none)."""
    return session.hard_expires_at or (session.created_at + _max_age())


def effective_expiry(session):
    """`expires_at`, or created_at + TTL for a legacy row that still has none
    (never 'valid forever'), capped by the absolute limit."""
    expiry = session.expires_at or (session.created_at + _ttl())
    return min(expiry, hard_limit(session))


def extract_session_token(request):
    """The credential may arrive in the `X-Widget-Session` header (preferred: not
    logged with the URL), the JSON/form body, or — legacy only — the query string."""
    token = request.headers.get(SESSION_HEADER)
    if token:
        return token
    data = getattr(request, 'data', None)
    token = data.get('session_token') if hasattr(data, 'get') else None
    if token:
        return token
    return request.query_params.get('session_token') if hasattr(request, 'query_params') else None


def get_valid_session(token, *, renew=True):
    """The live `VisitorSession` for `token`, or None.

    Valid means: exists, not revoked, not expired (sliding TTL, absolute max
    age), and its project and workspace are still active.
    """
    if not token:
        return None
    try:
        session = VisitorSession.objects.select_related('visitor__project__workspace').get(token=token)
    except (VisitorSession.DoesNotExist, ValidationError, ValueError):
        return None
    now = timezone.now()
    if session.revoked_at is not None or now >= effective_expiry(session):
        return None
    project = session.visitor.project
    if not (project.is_active and project.workspace.is_active):
        return None
    if renew:
        _renew(session, now)
    return session


def _renew(session, now):
    """Slide the expiry forward on use, at most once per half-TTL (so a busy
    chat is one write, not one per message), never beyond the absolute max age."""
    ttl = _ttl()
    current = session.expires_at
    if current is not None and current - now > ttl / 2:
        return
    new_expiry = min(now + ttl, hard_limit(session))
    if current is None or new_expiry > current:
        session.expires_at = new_expiry
        session.save(update_fields=['expires_at'])


def revoke_session(session):
    if session.revoked_at is None:
        session.revoked_at = timezone.now()
        session.save(update_fields=['revoked_at'])


def rotate_session(session):
    """Issue a fresh token for the same session row (the old token stops working
    immediately). Expiry is renewed but the absolute max age is not reset."""
    import uuid
    now = timezone.now()
    session.token = uuid.uuid4()
    session.rotated_at = now
    session.expires_at = min(now + _ttl(), hard_limit(session))
    session.save(update_fields=['token', 'rotated_at', 'expires_at'])
    return session


def rotation_due(session):
    since = session.rotated_at or session.created_at
    return timezone.now() - since >= timedelta(hours=settings.VISITOR_SESSION_ROTATE_AFTER_HOURS)
