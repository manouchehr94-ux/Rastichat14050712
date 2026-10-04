"""Endpoints that mint single-use WebSocket tickets (see common/ws_tickets.py).

A ticket is only issued for a socket the consumer would accept right now (same
`ws_access` rules), and is bound to the caller, the connection kind and the
exact conversation. Unauthorized targets get a uniform 404 so the endpoint is
not an oracle for conversation ids.
"""
import redis as redis_lib
from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from common import ws_tickets
from visitors.sessions import extract_session_token, get_valid_session

from . import ws_access

_NOT_FOUND = {'error': 'Not found'}


def _issue(**kwargs):
    try:
        ticket = ws_tickets.issue_ticket(**kwargs)
    except redis_lib.RedisError:
        return Response({'error': 'Realtime service unavailable'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({'ticket': ticket, 'expires_in': settings.WS_TICKET_TTL_SECONDS}, status=status.HTTP_201_CREATED)


class DashboardWsTicketView(APIView):
    """`POST /api/v1/ws/ticket/ {kind: dashboard_chat|support|notifications, conversation_id}` (JWT)."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ws_ticket'

    def post(self, request):
        kind = request.data.get('kind')
        if kind == ws_tickets.KIND_NOTIFICATIONS:
            return _issue(kind=kind, subject=request.user.id)
        if kind not in (ws_tickets.KIND_DASHBOARD_CHAT, ws_tickets.KIND_SUPPORT):
            return Response({'error': 'Invalid kind'}, status=status.HTTP_400_BAD_REQUEST)
        conv_id = request.data.get('conversation_id')
        if kind == ws_tickets.KIND_DASHBOARD_CHAT:
            conv = ws_access.operator_conversation(request.user, conv_id)
        else:
            conv = ws_access.support_conversation(request.user, conv_id)
        if conv is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return _issue(kind=kind, subject=request.user.id, scope_id=conv.id)


class WidgetWsTicketView(APIView):
    """`POST /api/v1/widget/ws-ticket/ {conversation_id}` + the visitor's session credential."""
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'ws_ticket'

    def post(self, request):
        session = get_valid_session(extract_session_token(request))
        if session is None:
            return Response({'error': 'Invalid session', 'code': 'session_invalid'}, status=status.HTTP_401_UNAUTHORIZED)
        conv = ws_access.visitor_conversation(session, request.data.get('conversation_id'))
        if conv is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return _issue(kind=ws_tickets.KIND_WIDGET, subject=session.id, scope_id=conv.id)
