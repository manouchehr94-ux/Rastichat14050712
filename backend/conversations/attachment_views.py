from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import attachment_access as access
from .models import Message


def _with_attachment():
    return Message.objects.select_related('conversation__workspace__platform', 'conversation__project', 'conversation__visitor') \
        .filter(attachment__isnull=False).exclude(Q(attachment=''))


class AttachmentDownloadView(APIView):
    """GET /attachments/<message_id>/?sig=…  — the only door to a chat attachment. Every refusal is the same 404."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'attachment_download'
    http_method_names = ['get', 'head']

    def get(self, request, message_id):
        payload = access.read_token(request.query_params.get('sig'))
        if payload is None or payload['m'] != str(message_id):
            return Response(status=status.HTTP_404_NOT_FOUND)
        message = _with_attachment().filter(id=message_id).first()
        if message is None or not access.authorize(payload, message):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return access.serve(message)


class AttachmentRefreshView(APIView):
    """GET /attachments/<message_id>/refresh/ — a fresh signed URL for whoever is asking: a signed-in staff member (JWT header) or
    a live visitor session (`X-Widget-Session` header). Clients call it when a URL they hold has expired, so the 10-minute lifetime
    never shows the user a broken image. The identity is checked against the message's conversation exactly as a fetch would."""
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'attachment_url'

    def get(self, request, message_id):
        audience = access.audience_from_request(request)
        if audience is None:
            if access.extract_session_token(request):
                return Response({'error': 'Invalid session', 'code': 'session_invalid'}, status=status.HTTP_401_UNAUTHORIZED)
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        message = _with_attachment().filter(id=message_id).first()
        kind, subject = audience
        if message is None or not access.authorize({'a': kind, 'i': None if subject is None else str(subject)}, message):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({'attachment_url': access.url_for(message, audience, request), 'expires_in': access.ttl()})
