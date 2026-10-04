"""Cross-cutting HTTP middleware: request correlation IDs and a small,
Django-4.2-compatible set of security response headers.

Django's own CSP support only landed in 5.1; this project is pinned to 4.2,
so the Content-Security-Policy header below is hand-rolled rather than
relying on a Django built-in.
"""
import logging
import threading
import uuid

from django.conf import settings

_request_id_local = threading.local()


def get_current_request_id():
    return getattr(_request_id_local, 'value', None) or '-'


class RequestIDLogFilter(logging.Filter):
    """Injects the current request's correlation ID into every log record
    emitted while that request is being handled, so `LOGGING`'s formatter
    can include %(request_id)s even for log calls deep inside a view or
    service function that never sees the request object directly.
    """

    def filter(self, record):
        record.request_id = get_current_request_id()
        return True


class RequestIDMiddleware:
    """Assigns a short correlation ID to every request — reusing an
    upstream `X-Request-ID` from Nginx when present so a single request can
    be traced end-to-end from the proxy access log through to Django's
    application log, and generating one otherwise (e.g. local dev with no
    proxy in front). Echoed back on the response so a client/operator can
    quote it when reporting an issue.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get('HTTP_X_REQUEST_ID', '').strip()[:64] or uuid.uuid4().hex[:16]
        request.request_id = request_id
        _request_id_local.value = request_id
        try:
            response = self.get_response(request)
        finally:
            _request_id_local.value = None
        response['X-Request-ID'] = request_id
        return response


class SecurityHeadersMiddleware:
    """Adds the response headers Django's own SecurityMiddleware doesn't
    cover: Content-Security-Policy (no built-in support until Django 5.1)
    and Permissions-Policy. Applied unconditionally — these headers are
    inert/no-op on a plain-HTTP local dev server and safe to always send.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('Content-Security-Policy', settings.CONTENT_SECURITY_POLICY)
        # This backend (admin + JSON/WS API) never itself requests any of
        # these — the Widget's own microphone use for voice messages happens
        # on the embedding third-party page, governed by that page's own
        # headers, not this origin's.
        response.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
        return response
