from rest_framework import serializers
from .models import User, OperatorPresence

class UserSerializer(serializers.ModelSerializer):
    presence_status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'display_name', 'avatar_url', 'title', 'presence_status']

    def get_presence_status(self, obj):
        presence = getattr(obj, 'presence', None)
        return presence.effective_status() if presence else OperatorPresence.Status.OFFLINE

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

class PresenceSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OperatorPresence.Status.choices)
    max_capacity = serializers.IntegerField(required=False, min_value=1, max_value=100)


class PresenceDetailSerializer(serializers.Serializer):
    """Fuller presence snapshot for the supervisor dashboard / inbox filters
    (unlike PresenceSerializer, which is only the request/response shape for
    the operator's own explicit status change).
    """
    status = serializers.CharField(source='effective_status')
    max_capacity = serializers.IntegerField()
    active_conversation_count = serializers.SerializerMethodField()

    def get_active_conversation_count(self, obj):
        return obj.active_conversation_count()
