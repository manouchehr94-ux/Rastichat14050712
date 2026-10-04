import uuid
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from projects.models import Project
from .models import Visitor, VisitorSession
from .serializers import VisitorInitSerializer

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

        if external_id:
            # A known/identified customer (e.g. logged into the tenant's own
            # site): resolve to the same Visitor across sessions.
            visitor, created = Visitor.objects.get_or_create(
                project=project, external_id=external_id, defaults=attrs,
            )
        else:
            # Anonymous visitor: `project` alone is not a unique identity, so
            # get_or_create here would collapse every anonymous session for
            # this project onto the same Visitor/conversation. Each anonymous
            # session gets its own Visitor.
            visitor = Visitor.objects.create(project=project, **attrs)

        session = VisitorSession.objects.create(visitor=visitor)
        return Response({
            'visitor_id': str(visitor.id),
            'session_token': str(session.token)
        }, status=status.HTTP_200_OK)
