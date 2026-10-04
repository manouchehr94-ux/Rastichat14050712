"""Regression test for the WebSocket Origin allowlist added to
config/asgi.py. Browsers don't apply CORS/same-origin restrictions to
WebSocket connections themselves, so without this any page on the internet
could open a socket straight to Daphne — this proves the validator actually
rejects a disallowed Origin, not just that nothing broke.

Builds its own OriginValidator-wrapped application with an explicit
restricted allowlist (rather than relying on config.asgi.application, which
bakes settings.CORS_ALLOWED_ORIGINS in once at import time and can't be
reconfigured per-test via override_settings) — same construction
(OriginValidator -> AuthMiddlewareStack -> URLRouter) config/asgi.py uses,
just with a deterministic allowlist for the test.
"""
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.security.websocket import OriginValidator
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase

from config.routing import websocket_urlpatterns
from knowledge_base.tests_base import KBTestMixin

_restricted_application = OriginValidator(
    AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    ['https://operator-chat-staging.rastisi.ir'],
)


class WebSocketOriginValidationTests(KBTestMixin, TransactionTestCase):
    def setUp(self):
        self.ws = self.make_workspace()
        self.project = self.make_project(self.ws)
        self.visitor = self.make_visitor(self.project)
        self.session = self.make_visitor_session(self.visitor)
        self.conv = self.make_conversation(self.ws, self.project, self.visitor)

    async def test_disallowed_origin_is_rejected(self):
        communicator = WebsocketCommunicator(
            _restricted_application, f"/ws/widget/{self.session.token}/{self.conv.id}/",
            headers=[(b'origin', b'https://evil-attacker.example.com')],
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_allowed_origin_still_connects(self):
        communicator = WebsocketCommunicator(
            _restricted_application, f"/ws/widget/{self.session.token}/{self.conv.id}/",
            headers=[(b'origin', b'https://operator-chat-staging.rastisi.ir')],
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()
