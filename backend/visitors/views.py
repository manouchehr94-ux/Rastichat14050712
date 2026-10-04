import logging
import uuid
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response
from rest_framework import status
from projects.models import Project
from .models import Visitor, VisitorSession
from .serializers import VisitorInitSerializer
from .sessions import get_valid_session, extract_session_token, revoke_session, rotate_session, enforce_project_origin

logger = logging.getLogger(__name__)


class InitVisitorView(APIView):
    permission_classes = [] # Public endpoint for widget
    # every call creates a Visitor + session row: unthrottled it is a free way to bloat the database
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'widget_init'

    def post(self, request):
        serializer = VisitorInitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        project = Project.objects.select_related('workspace').get(public_key=serializer.validated_data['project_key'])
        if not project.workspace.is_active:
            return Response({'project_key': ['Invalid or inactive project key.']}, status=status.HTTP_400_BAD_REQUEST)
        # session creation is the one call that carries no credential yet, so the Origin policy is strictest here
        enforce_project_origin(request, project, establishing=True)
        external_id = serializer.validated_data.get('external_id')
        attrs = {
            'name': serializer.validated_data.get('name'),
            'email': serializer.validated_data.get('email'),
            'mobile': serializer.validated_data.get('mobile'),
        }

        if external_id and settings.WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID:
            # LEGACY, insecure bridge (off by default): resolves to an existing
            # Visitor on a browser-supplied identifier. See settings.py.
            logger.warning(
                'widget init used legacy unverified external_id lookup (project=%s)', project.pk,
            )
            visitor, created = Visitor.objects.get_or_create(
                project=project, external_id=external_id, defaults=attrs,
            )
        else:
            # Anonymous OR unverified-identity visitor: `project` alone is not
            # a unique identity and a browser-submitted `external_id` is not
            # proof of one, so every init gets its own Visitor. A claimed
            # external_id is kept only as an unverified hint in `metadata`
            # (never in `Visitor.external_id`, which is reserved for identities
            # asserted by a trusted server). A returning visitor keeps their
            # history through their own session_token, not through this lookup.
            metadata = {'unverified_external_id': external_id[:255]} if external_id else {}
            visitor = Visitor.objects.create(project=project, metadata=metadata, **attrs)

        session = VisitorSession.objects.create(visitor=visitor)
        return Response({
            'visitor_id': str(visitor.id),
            'session_token': str(session.token),
            'expires_at': session.expires_at,
        }, status=status.HTTP_200_OK)


class RevokeVisitorSessionView(APIView):
    """Customer logout: the session token stops working immediately (REST and
    WebSocket). Idempotent — an unknown/expired/already-revoked token is a 204
    too, so the endpoint reveals nothing about token validity. The Visitor and
    their conversations are kept (operators still see the history)."""
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'widget_session'

    def post(self, request):
        session = get_valid_session(extract_session_token(request), renew=False)
        if session is not None:
            revoke_session(session)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RotateVisitorSessionView(APIView):
    """Swap a valid session's token for a fresh one; the old token is dead at once."""
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'widget_session'

    def post(self, request):
        session = get_valid_session(extract_session_token(request), renew=False)
        if session is None:
            return Response({'error': 'Invalid session', 'code': 'session_invalid'}, status=status.HTTP_401_UNAUTHORIZED)
        rotate_session(session)
        return Response({'session_token': str(session.token), 'expires_at': session.expires_at})
