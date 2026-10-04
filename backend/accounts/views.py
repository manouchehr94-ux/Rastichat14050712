from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from .serializers import UserSerializer, PresenceSerializer
from .presence import touch_presence

class LoginView(APIView):
    permission_classes = []
    # IP-scoped (the requester is anonymous to DRF at this point) — the
    # classic credential-stuffing/brute-force target.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user': UserSerializer(user).data
            })
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

class LogoutView(APIView):
    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            RefreshToken(refresh_token).blacklist()
            return Response(status=status.HTTP_205_RESET_CONTENT)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)

class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)

class PresenceView(APIView):
    """Lets an operator explicitly set their own online/away/offline status
    (e.g. the dashboard's status dropdown). Activity elsewhere (opening a
    conversation) only refreshes the timestamp — it never silently flips an
    operator who explicitly went offline back to online.
    """
    def post(self, request):
        serializer = PresenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        presence = touch_presence(request.user, explicit_status=serializer.validated_data['status'])
        if 'max_capacity' in serializer.validated_data:
            presence.max_capacity = serializer.validated_data['max_capacity']
            presence.save(update_fields=['max_capacity'])
        from notifications.services import broadcast_presence_updated
        workspace_ids = list(request.user.workspace_memberships.values_list('workspace_id', flat=True))
        broadcast_presence_updated(request.user, workspace_ids, presence.effective_status())
        return Response({
            'status': presence.effective_status(), 'max_capacity': presence.max_capacity,
            'active_conversation_count': presence.active_conversation_count(),
        })

    def get(self, request):
        presence = touch_presence(request.user)
        return Response({
            'status': presence.effective_status(), 'max_capacity': presence.max_capacity,
            'active_conversation_count': presence.active_conversation_count(),
        })
