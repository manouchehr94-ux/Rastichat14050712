"""`Idempotency-Key` handling for non-idempotent integration calls (Contract v1 §5)."""
import hashlib
import json

from django.db import IntegrityError, transaction
from rest_framework.response import Response

from .errors import IntegrationAPIError
from .models import IdempotencyRecord

MAX_KEY = 128


def require_key(request):
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key or len(key) > MAX_KEY:
        raise IntegrationAPIError('idempotency_key_required', f'An Idempotency-Key header (<= {MAX_KEY} chars) is required.', 400)
    return key


def run_once(request, integration, endpoint, payload, perform):
    """Execute `perform() -> (status, body)` at most once per (integration, key).

    A retry with the same key and payload returns the first response (header `Idempotent-Replayed: true`); the same key
    with a different payload or endpoint -> 409 `idempotency_conflict`. Failed attempts are not stored, so they can be
    retried with the same key. Concurrent identical requests converge: the unique constraint serialises them.
    """
    key = require_key(request)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def replay(record):
        if record.request_hash != digest or record.endpoint != endpoint:
            raise IntegrationAPIError('idempotency_conflict', 'This Idempotency-Key was used with a different request.', 409)
        response = Response(record.response_body, status=record.status_code)
        response['Idempotent-Replayed'] = 'true'
        return response

    existing = IdempotencyRecord.objects.filter(integration=integration, key=key).first()
    if existing:
        return replay(existing)
    try:
        with transaction.atomic():
            status, body = perform()
            IdempotencyRecord.objects.create(integration=integration, key=key, endpoint=endpoint, request_hash=digest,
                                             status_code=status, response_body=body)
    except IntegrityError:
        return replay(IdempotencyRecord.objects.get(integration=integration, key=key))
    return Response(body, status=status)
