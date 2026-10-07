"""The Integration Contract v1 error model: every error is `{"error": {"code", "message", "details"?, "request_id"?}}`
with a stable, machine-readable `code`."""
from rest_framework import status as http
from rest_framework.exceptions import APIException


class IntegrationAPIError(APIException):
    status_code = http.HTTP_400_BAD_REQUEST
    default_code = 'bad_request'

    def __init__(self, code=None, message='Request failed.', status=None, details=None):
        super().__init__(detail=message, code=code or self.default_code)
        if status is not None:
            self.status_code = status
        self.error_code = code or self.default_code
        self.message = message
        self.details = details


class TenantArchived(IntegrationAPIError):
    def __init__(self):
        super().__init__('tenant_archived', 'This tenant is archived; send status "active" to restore it.',
                         http.HTTP_409_CONFLICT)
