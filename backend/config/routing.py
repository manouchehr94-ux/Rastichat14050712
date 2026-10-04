from django.urls import re_path
from conversations.consumers import WidgetChatConsumer, DashboardChatConsumer, DashboardSupportConsumer
from notifications.consumers import NotificationsConsumer

websocket_urlpatterns = [
    re_path(r'ws/widget/(?P<session_token>[0-9a-f-]+)/(?P<conv_id>[0-9a-f-]+)/$', WidgetChatConsumer.as_asgi()),
    re_path(r'ws/dashboard/(?P<token>[^/]+)/(?P<conv_id>[0-9a-f-]+)/$', DashboardChatConsumer.as_asgi()),
    re_path(r'ws/dashboard/support/(?P<token>[^/]+)/(?P<conv_id>[0-9a-f-]+)/$', DashboardSupportConsumer.as_asgi()),
    re_path(r'ws/notifications/(?P<token>[^/]+)/$', NotificationsConsumer.as_asgi()),
]
