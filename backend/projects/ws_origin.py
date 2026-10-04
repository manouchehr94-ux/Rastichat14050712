"""WebSocket Origin validation that also honours projects' allowed domains for the WIDGET routes.

Same contract as channels' `OriginValidator` (static allow-list, no Origin => refused unless '*'), plus: on the visitor
widget routes an Origin configured on any active project is accepted at the handshake. The widget consumer then applies
the real per-project check (`decide_origin`) for the specific project the session belongs to.
"""
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from channels.security.websocket import OriginValidator, WebsocketDenier

from .domains import all_project_entries, origin_matches

WIDGET_WS_PREFIXES = ('/ws/widget/', '/ws/v2/widget/')


def _configured_on_some_project(origin):
    return origin_matches(origin, all_project_entries())


class ProjectAwareOriginValidator(OriginValidator):
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'websocket':
            raise ValueError('You cannot use ProjectAwareOriginValidator on a non-WebSocket connection')
        raw_origin, parsed_origin = None, None
        for header_name, header_value in scope.get('headers', []):
            if header_name == b'origin':
                raw_origin = header_value.decode('latin1')
                parsed_origin = urlparse(raw_origin)
        allowed = self.valid_origin(parsed_origin)
        if not allowed and raw_origin and scope.get('path', '').startswith(WIDGET_WS_PREFIXES):
            allowed = await sync_to_async(_configured_on_some_project, thread_sensitive=True)(raw_origin)
        if allowed:
            return await self.application(scope, receive, send)
        return await WebsocketDenier()(scope, receive, send)
