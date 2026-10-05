"""Private chat attachments: short-lived signed URLs, authorized on every fetch.

`attachment_url` used to be `/media/attachments/<uuid>.<ext>` — a bearer secret that nginx served to anyone, for ever, and that the
nginx access log recorded in full. It is now `/api/v1/attachments/<message_id>/?sig=<token>` where the token

* is signed (`django.core.signing`, own salt) and expires after `ATTACHMENT_URL_TTL_SECONDS` (default 10 minutes),
* names exactly one message and one AUDIENCE: `u` = a staff user, `v` = one visitor session, `c` = "this conversation's own visitor"
  (the only kind that can be put in a room broadcast, which every socket in the room receives),
* carries no permission by itself: on every fetch Django re-checks that identity's CURRENT access (user active + membership,
  session live, project/workspace/platform active) with the same rules the WebSockets use (`ws_access`), so removing a member,
  deactivating a user, revoking/expiring a session or deactivating a store closes access to NEW fetches at once.

Django only authorizes; nginx streams the file (`X-Accel-Redirect` to an `internal` location), so Range requests for voice notes and
large files work and no file passes through Python. Every refusal is the same 404.
"""
import mimetypes
import os

from django.conf import settings
from django.core import signing
from django.http import FileResponse, HttpResponse
from django.urls import reverse

from accounts.models import User
from visitors.models import VisitorSession
from visitors.sessions import _is_live, extract_session_token, get_valid_session, get_valid_session_by_id

from . import ws_access
from .models import Conversation

SALT = 'rastichat.chat-attachment.v1'
AUDIENCE_USER, AUDIENCE_VISITOR_SESSION, AUDIENCE_CONVERSATION_VISITOR = 'u', 'v', 'c'
AUDIENCES = (AUDIENCE_USER, AUDIENCE_VISITOR_SESSION, AUDIENCE_CONVERSATION_VISITOR)

# What validate_and_normalize_upload can store; anything else is served as an opaque download, never sniffed into a page.
CONTENT_TYPES = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
    '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.oga': 'audio/ogg', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4',
    '.mp4': 'audio/mp4', '.wav': 'audio/wav', '.aac': 'audio/aac',
}


def ttl():
    return int(getattr(settings, 'ATTACHMENT_URL_TTL_SECONDS', 600))


def make_token(message, audience, subject=None):
    if audience not in AUDIENCES:
        raise ValueError(f'unknown attachment audience {audience!r}')
    payload = {'m': str(message.id), 'a': audience, 'i': None if subject is None else str(subject)}
    return signing.dumps(payload, salt=SALT, compress=False)


def read_token(token):
    """The payload of a genuine, unexpired token, else None (tampered, expired, foreign key/salt, malformed)."""
    if not token or not isinstance(token, str):
        return None
    try:
        payload = signing.loads(token, salt=SALT, max_age=ttl())
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict) or payload.get('a') not in AUDIENCES or not payload.get('m'):
        return None
    return payload


def audience_from_request(request):
    """Who is asking: a signed-in staff user, or a live visitor session. None => nobody (no URL is issued)."""
    if request is None:
        return None
    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        return (AUDIENCE_USER, user.id)
    try:
        session = get_valid_session(extract_session_token(request), renew=False)
    except Exception:  # noqa: BLE001 - an unusable credential simply means "no URL"
        return None
    return (AUDIENCE_VISITOR_SESSION, session.id) if session is not None else None


def url_for(message, audience, request=None):
    kind, subject = audience
    path = reverse('attachment-download', kwargs={'message_id': message.id}) + '?sig=' + make_token(message, kind, subject)
    return request.build_absolute_uri(path) if request is not None else path


def _visitor_has_a_live_session(visitor):
    return any(_is_live(s, _now()) for s in VisitorSession.objects.filter(visitor=visitor).select_related('visitor__project__workspace'))


def _now():
    from django.utils import timezone
    return timezone.now()


def authorize(payload, message):
    """Does the identity named in the token have access to this message's conversation RIGHT NOW?"""
    conv = message.conversation
    kind, subject = payload['a'], payload.get('i')
    try:
        if kind == AUDIENCE_USER:
            user = User.objects.filter(id=subject, is_active=True).first()
            if conv.type == Conversation.Type.CUSTOMER:
                return ws_access.operator_conversation(user, conv.id) is not None
            if conv.type == Conversation.Type.PLATFORM_SUPPORT:
                return ws_access.support_conversation(user, conv.id) is not None
            return False
        if kind == AUDIENCE_VISITOR_SESSION:
            return ws_access.visitor_conversation(get_valid_session_by_id(subject), conv.id) is not None
        if kind == AUDIENCE_CONVERSATION_VISITOR:
            return conv.type == Conversation.Type.CUSTOMER and _visitor_has_a_live_session(conv.visitor)
    except (ValueError, TypeError):
        return False
    return False


def content_type_for(message):
    ext = os.path.splitext(message.attachment.name)[1].lower()
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(message.attachment.name)[0] or 'application/octet-stream'


def serve(message):
    """The authorized response: an X-Accel-Redirect for nginx (production), or the bytes themselves (development)."""
    content_type = content_type_for(message)
    mode = getattr(settings, 'ATTACHMENT_SERVE_MODE', 'accel')
    if mode == 'accel':
        response = HttpResponse(status=200, content_type=content_type)
        response['X-Accel-Redirect'] = settings.ATTACHMENT_ACCEL_PREFIX.rstrip('/') + '/' + message.attachment.name.lstrip('/')
    else:
        response = FileResponse(message.attachment.open('rb'), content_type=content_type)
    response['Content-Disposition'] = 'inline'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Referrer-Policy'] = 'no-referrer'
    return response
