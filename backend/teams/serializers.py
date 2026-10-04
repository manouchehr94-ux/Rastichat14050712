from rest_framework import serializers
from .models import Team, TeamMembership


class TeamMemberSerializer(serializers.ModelSerializer):
    user_id = serializers.CharField(source='user.id', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = TeamMembership
        fields = ['id', 'user_id', 'email', 'display_name', 'role', 'is_active', 'created_at']

    def get_display_name(self, obj):
        return obj.user.display_name or obj.user.email.split('@')[0]


class TeamSerializer(serializers.ModelSerializer):
    members = TeamMemberSerializer(source='memberships', many=True, read_only=True)
    manager_email = serializers.CharField(source='manager.email', read_only=True, default=None)
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = [
            'id', 'workspace', 'name', 'description', 'is_active', 'manager', 'manager_email',
            'members', 'member_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'workspace', 'created_at', 'updated_at']

    def get_member_count(self, obj):
        return obj.memberships.filter(is_active=True).count()

    def validate(self, attrs):
        manager = attrs.get('manager', self.instance.manager if self.instance else None)
        workspace = self.instance.workspace if self.instance else self.context.get('workspace')
        if manager and workspace and not manager.workspace_memberships.filter(workspace=workspace).exists():
            raise serializers.ValidationError({'manager': 'Manager must belong to the same workspace.'})
        return attrs
