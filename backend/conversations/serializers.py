from rest_framework import serializers
from .models import Conversation, Message, Assignment, PriorityChange

class MessageSerializer(serializers.ModelSerializer):
    attachment_url = serializers.SerializerMethodField()
    seen = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender_type', 'content', 'message_type', 'metadata',
            'attachment_url', 'client_message_id', 'created_at', 'seen',
        ]
        read_only_fields = [
            'id', 'created_at', 'sender_type', 'conversation', 'message_type', 'metadata',
            'attachment_url', 'seen',
        ]

    def get_attachment_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get('request')
        url = obj.attachment.url
        return request.build_absolute_uri(url) if request else url

    def get_seen(self, obj):
        # A message is "seen" once someone from the other side of the conversation has read it.
        if obj.sender_type == Message.SenderType.VISITOR:
            return obj.receipts.filter(user__isnull=False).exists()
        return obj.receipts.filter(visitor__isnull=False).exists()


class AssignmentSerializer(serializers.ModelSerializer):
    assigned_to_email = serializers.CharField(source='assigned_to.email', read_only=True, default=None)
    assigned_by_email = serializers.CharField(source='assigned_by.email', read_only=True, default=None)
    previous_assignee_email = serializers.CharField(source='previous_assignee.email', read_only=True, default=None)
    previous_team_name = serializers.CharField(source='previous_team.name', read_only=True, default=None)
    new_team_name = serializers.CharField(source='new_team.name', read_only=True, default=None)

    class Meta:
        model = Assignment
        fields = [
            'id', 'conversation', 'action', 'assigned_to', 'assigned_to_email', 'assigned_by', 'assigned_by_email',
            'previous_assignee', 'previous_assignee_email', 'previous_team', 'previous_team_name',
            'new_team', 'new_team_name', 'reason', 'created_at',
        ]


class PriorityChangeSerializer(serializers.ModelSerializer):
    changed_by_email = serializers.CharField(source='changed_by.email', read_only=True, default=None)

    class Meta:
        model = PriorityChange
        fields = [
            'id', 'conversation', 'previous_priority', 'priority', 'reason_code', 'reason',
            'changed_by', 'changed_by_email', 'changed_at',
        ]


class ConversationSLASummarySerializer(serializers.Serializer):
    """Read-only SLA snapshot embedded in the conversation payload for inbox
    countdown/badge rendering — never editable through this serializer.
    """
    first_response_due_at = serializers.DateTimeField(allow_null=True)
    next_response_due_at = serializers.DateTimeField(allow_null=True)
    resolution_due_at = serializers.DateTimeField(allow_null=True)
    first_responded_at = serializers.DateTimeField(allow_null=True)
    resolved_at = serializers.DateTimeField(allow_null=True)
    first_response_breached_at = serializers.DateTimeField(allow_null=True)
    next_response_breached_at = serializers.DateTimeField(allow_null=True)
    resolution_breached_at = serializers.DateTimeField(allow_null=True)


class ConversationSerializer(serializers.ModelSerializer):
    unread_count = serializers.SerializerMethodField()
    visitor = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    assigned_to = serializers.SerializerMethodField()
    team_name = serializers.CharField(source='team.name', read_only=True, default=None)
    queue_name = serializers.CharField(source='queue.name', read_only=True, default=None)
    sla = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id', 'type', 'status', 'subject', 'category', 'notes', 'rating', 'priority',
            'queue', 'queue_name', 'team', 'team_name',
            'created_at', 'updated_at', 'closed_at', 'unread_count', 'visitor', 'last_message', 'assigned_to', 'sla',
        ]
        read_only_fields = [
            'id', 'type', 'status', 'created_at', 'updated_at', 'closed_at', 'unread_count',
            'rating', 'visitor', 'last_message', 'assigned_to', 'queue_name', 'team_name', 'sla',
        ]

    def get_assigned_to(self, obj):
        if not obj.assigned_to:
            return None
        u = obj.assigned_to
        return {'id': str(u.id), 'display_name': u.display_name or u.email.split('@')[0], 'email': u.email}

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 0
        return obj.messages.exclude(receipts__user=request.user).exclude(
            message_type=Message.MessageType.INTERNAL_NOTE,
        ).count()

    def get_visitor(self, obj):
        if not obj.visitor:
            return None
        v = obj.visitor
        return {
            'id': str(v.id),
            'name': v.name,
            'email': v.email,
            'mobile': v.mobile,
            'created_at': v.created_at,
        }

    def get_last_message(self, obj):
        msg = obj.messages.exclude(message_type=Message.MessageType.INTERNAL_NOTE).order_by('-created_at').first()
        if not msg:
            return None
        return {
            'content': msg.content,
            'message_type': msg.message_type,
            'sender_type': msg.sender_type,
            'created_at': msg.created_at,
        }

    def get_sla(self, obj):
        sla = getattr(obj, 'sla', None)
        if not sla:
            return None
        return ConversationSLASummarySerializer(sla).data
