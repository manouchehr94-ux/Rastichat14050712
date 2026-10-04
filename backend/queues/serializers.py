from rest_framework import serializers
from .models import Queue


class QueueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Queue
        fields = [
            'id', 'workspace', 'team', 'name', 'is_active', 'routing_priority', 'supported_categories',
            'default_priority', 'assignment_strategy', 'max_active_per_agent', 'fallback_queue',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'workspace', 'created_at', 'updated_at']

    def validate(self, attrs):
        workspace = self.instance.workspace if self.instance else self.context.get('workspace')
        team = attrs.get('team', self.instance.team if self.instance else None)
        if team and workspace and team.workspace_id != workspace.id:
            raise serializers.ValidationError({'team': 'Team must belong to the same workspace as the queue.'})

        fallback_queue = attrs.get('fallback_queue', self.instance.fallback_queue if self.instance else None)
        if fallback_queue:
            if workspace and fallback_queue.workspace_id != workspace.id:
                raise serializers.ValidationError({'fallback_queue': 'Fallback queue must belong to the same workspace.'})
            if self.instance:
                seen = {self.instance.id}
                cursor = fallback_queue
                for _ in range(10):
                    if cursor.id in seen:
                        raise serializers.ValidationError({'fallback_queue': 'Fallback queue chain must not loop.'})
                    seen.add(cursor.id)
                    cursor = cursor.fallback_queue
                    if not cursor:
                        break
                else:
                    raise serializers.ValidationError({'fallback_queue': 'Fallback queue chain is too long.'})
        return attrs
