import logging
import uuid
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from projects.models import Project
from .models import Visitor, VisitorSession
from .serializers import VisitorInitSerializer

logger = logging.getLogger(__name__)


class InitVisitorView(APIView):
    permission_classes = [] # Public endpoint for widget

    def post(self, request):
        serializer = VisitorInitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        project = Project.objects.get(public_key=serializer.validated_data['project_key'])
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
            'session_token': str(session.token)
        }, status=status.HTTP_200_OK)
