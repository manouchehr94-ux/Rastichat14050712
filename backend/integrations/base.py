"""Shared response behaviour of every Integration Contract endpoint: the v1 error envelope and contract headers."""
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from common.middleware import get_current_request_id

from .errors import IntegrationAPIError

CONTRACT_HEADER = 'X-RastiChat-Contract'
CONTRACT_VERSION = 'integration-v1'


class ContractEnvelopeMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response[CONTRACT_HEADER] = CONTRACT_VERSION
        response['Cache-Control'] = 'no-store'
        return response

    def handle_exception(self, exc):
        # Any error leaves in the v1 envelope; unexpected exceptions keep DRF/Django's own 500 handling.
        if isinstance(exc, IntegrationAPIError):
            return self._envelope(exc.status_code, exc.error_code, exc.message, exc.details)
        if isinstance(exc, ValidationError):
            return self._envelope(status.HTTP_400_BAD_REQUEST, 'validation_error', 'Invalid request.', exc.detail)
        if isinstance(exc, APIException):
            detail = exc.detail
            code = detail.get('code') if isinstance(detail, dict) and detail.get('code') else {
                401: 'unauthenticated', 403: 'forbidden', 404: 'not_found', 405: 'method_not_allowed',
                429: 'rate_limited', 415: 'unsupported_media_type'}.get(exc.status_code, 'error')
            message = detail.get('error') if isinstance(detail, dict) and detail.get('error') else str(detail)
            response = self._envelope(exc.status_code, code, message)
            if exc.status_code == 429 and getattr(exc, 'wait', None):
                response['Retry-After'] = str(int(exc.wait) + 1)
            return response
        return super().handle_exception(exc)

    @staticmethod
    def _envelope(http_status, code, message, details=None):
        body = {'code': code, 'message': message, 'request_id': get_current_request_id()}
        if details:
            body['details'] = details
        return Response({'error': body}, status=http_status)
