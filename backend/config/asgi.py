import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()  # Ensure Django is fully configured before importing routing

from django.conf import settings
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import OriginValidator
from config.routing import websocket_urlpatterns

# Browsers do not apply same-origin/CORS restrictions to WebSocket
# connections the way they do to fetch()/XHR — without an explicit check
# here, any page on the internet could open a WS connection to this server
# (the per-message auth/workspace-membership checks in
# conversations/consumers.py still gate what a connection can actually see
# or do, but an unvalidated Origin is an unnecessary extra attack surface,
# e.g. for reconnaissance or resource exhaustion). Reuses the exact same
# allowlist as REST CORS (settings.CORS_ALLOWED_ORIGINS) rather than a
# second, independently-maintained list — '*' only when CORS itself is
# wide open (bare local development; settings.py already refuses to start
# staging/production with an empty CORS_ALLOWED_ORIGINS).
_ws_allowed_origins = settings.CORS_ALLOWED_ORIGINS or ['*']

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": OriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
        _ws_allowed_origins,
    ),
})
