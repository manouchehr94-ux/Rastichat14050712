from django.urls import re_path
from conversations.consumers import WidgetChatConsumer, DashboardChatConsumer, DashboardSupportConsumer
from notifications.consumers import NotificationsConsumer

websocket_urlpatterns = [
    # Ticket-authenticated routes: NO credential in the URL — the client sends a single-use
    # ticket (common/ws_tickets.py) as its first frame. These are the supported routes.
    re_path(r'ws/v2/widget/(?P<conv_id>[0-9a-f-]+)/$', WidgetChatConsumer.as_asgi()),
    re_path(r'ws/v2/dashboard/(?P<conv_id>[0-9a-f-]+)/$', DashboardChatConsumer.as_asgi()),
    re_path(r'ws/v2/support/(?P<conv_id>[0-9a-f-]+)/$', DashboardSupportConsumer.as_asgi()),
    re_path(r'ws/v2/notifications/$', NotificationsConsumer.as_asgi()),
    # Legacy token-in-path routes: refused unless settings.LEGACY_URL_CREDENTIALS_ENABLED.
    re_path(r'ws/widget/(?P<session_token>[0-9a-f-]+)/(?P<conv_id>[0-9a-f-]+)/$', WidgetChatConsumer.as_asgi()),
    re_path(r'ws/dashboard/(?P<token>[^/]+)/(?P<conv_id>[0-9a-f-]+)/$', DashboardChatConsumer.as_asgi()),
    re_path(r'ws/dashboard/support/(?P<token>[^/]+)/(?P<conv_id>[0-9a-f-]+)/$', DashboardSupportConsumer.as_asgi()),
    re_path(r'ws/notifications/(?P<token>[^/]+)/$', NotificationsConsumer.as_asgi()),
]
