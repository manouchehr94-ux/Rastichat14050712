from rest_framework import serializers
from .models import Visitor, VisitorSession
from projects.models import Project

class VisitorSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VisitorSession
        fields = ['id', 'token']

class VisitorInitSerializer(serializers.Serializer):
    project_key = serializers.UUIDField()
    name = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    mobile = serializers.CharField(required=False, allow_blank=True)
    # Stable identifier the embedding site can pass for a known/logged-in
    # customer, so returning visitors resolve to the same Visitor record.
    # Left blank for anonymous visitors — see InitVisitorView.
    external_id = serializers.CharField(required=False, allow_blank=True)

    def validate_project_key(self, value):
        if not Project.objects.filter(public_key=value, is_active=True).exists():
            raise serializers.ValidationError("Invalid or inactive project key.")
        return value
