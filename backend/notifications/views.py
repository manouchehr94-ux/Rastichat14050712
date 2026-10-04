from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from common.pagination import StandardPagination
from common.permissions import IsWorkspaceOperator
from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """A user's own notifications only — never another user's, even within
    the same workspace. Read-only except for the mark-read actions below.
    """
    serializer_class = NotificationSerializer
    permission_classes = [IsWorkspaceOperator]
    pagination_class = StandardPagination

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        return Response({'count': self.get_queryset().filter(read_at__isnull=True).count()})

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        if not notif.read_at:
            notif.read_at = timezone.now()
            notif.save(update_fields=['read_at'])
        return Response(NotificationSerializer(notif).data)

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=status.HTTP_200_OK)
