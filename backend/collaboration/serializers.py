from rest_framework import serializers
from conversations.models import Message
from .models import Mention, QuickReply


class MentionSerializer(serializers.ModelSerializer):
    mentioned_user_email = serializers.CharField(source='mentioned_user.email', read_only=True)
    mentioned_user_display_name = serializers.SerializerMethodField()

    class Meta:
        model = Mention
        fields = ['id', 'note', 'mentioned_user', 'mentioned_user_email', 'mentioned_user_display_name', 'created_at']

    def get_mentioned_user_display_name(self, obj):
        return obj.mentioned_user.display_name or obj.mentioned_user.email.split('@')[0]


class InternalNoteSerializer(serializers.ModelSerializer):
    """Renders an internal-note Message plus its mentions. Never used for
    any visitor-facing endpoint — the view layer is what guarantees that.
    """
    sender_email = serializers.CharField(source='sender.email', read_only=True, default=None)
    sender_display_name = serializers.SerializerMethodField()
    mentions = MentionSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'content', 'message_type', 'sender_email', 'sender_display_name',
            'client_message_id', 'created_at', 'mentions',
        ]

    def get_sender_display_name(self, obj):
        if not obj.sender:
            return None
        return obj.sender.display_name or obj.sender.email.split('@')[0]


class QuickReplySerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(source='created_by.email', read_only=True, default=None)

    class Meta:
        model = QuickReply
        fields = [
            'id', 'workspace', 'scope', 'owner', 'team', 'title', 'body', 'shortcut', 'category',
            'is_active', 'usage_count', 'created_by', 'created_by_email', 'updated_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'workspace', 'usage_count', 'created_by', 'updated_by', 'created_at', 'updated_at']

    def validate(self, attrs):
        # scope/owner/team define WHO is authorized to manage this reply —
        # letting them change post-creation would let an update silently
        # re-target a reply's authorization without the create-time checks
        # (e.g. PATCHing a personal reply's scope to WORKSPACE) ever running
        # against the new target. They're fixed for the reply's lifetime;
        # scope changes require deleting and recreating.
        if self.instance:
            for immutable_field in ('scope', 'owner', 'team'):
                if immutable_field in attrs and attrs[immutable_field] != getattr(self.instance, immutable_field):
                    raise serializers.ValidationError({immutable_field: 'Cannot be changed after creation.'})
        return attrs
