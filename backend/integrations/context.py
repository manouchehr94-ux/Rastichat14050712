"""Host-pushed customer context (Contract v1 §11).

One validator for both ways a host can supply it: the `PUT …/contexts/{user}/` API and the optional `ctx` claim of a
customer identity assertion. Strict on purpose — flat scalar values only, small, no credential-looking keys or values —
because everything stored here is shown to operators and survives for the life of the customer identity.
"""
import json
import re

from django.db import transaction

from .errors import IntegrationAPIError
from .models import ExternalContext

SECTIONS = ('profile', 'context')
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
MAX_KEYS_PER_SECTION = 20
MAX_VALUE_CHARS = 200
MAX_TOTAL_BYTES = 4096
# keys that name credentials / payment data: refused so a careless host cannot start shipping them
_FORBIDDEN_KEY_PARTS = ('password', 'passwd', 'secret', 'token', 'authorization', 'api_key', 'apikey', 'private_key',
                        'cookie', 'session', 'card', 'cvv', 'iban', 'otp')
# values that look like bearer credentials (JWT, long opaque secrets) are refused too
_JWT_LIKE = re.compile(r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _bad(message, code='invalid_context'):
    raise IntegrationAPIError(code, message, 400)


def _clean_section(name, value):
    if not isinstance(value, dict):
        _bad(f'"{name}" must be an object of flat key/value pairs.')
    if len(value) > MAX_KEYS_PER_SECTION:
        _bad(f'"{name}" may have at most {MAX_KEYS_PER_SECTION} keys.')
    out = {}
    for key, val in value.items():
        if not isinstance(key, str) or not KEY_RE.match(key):
            _bad(f'"{name}": keys are lower-case letters, digits and underscores (max 40), starting with a letter.')
        if any(part in key for part in _FORBIDDEN_KEY_PARTS):
            _bad(f'"{name}.{key}": this looks like a credential or payment field, which must never be sent.',
                 'sensitive_context_not_accepted')
        if isinstance(val, bool) or val is None:
            if val is None:
                _bad(f'"{name}.{key}": null is not allowed — omit the key instead.')
            out[key] = val
        elif isinstance(val, (int, float)):
            out[key] = val
        elif isinstance(val, str):
            if len(val) > MAX_VALUE_CHARS:
                _bad(f'"{name}.{key}": at most {MAX_VALUE_CHARS} characters.')
            if _CONTROL.search(val) or _JWT_LIKE.search(val):
                _bad(f'"{name}.{key}": value contains control characters or looks like a credential.',
                     'sensitive_context_not_accepted')
            out[key] = val
        else:
            _bad(f'"{name}.{key}": only strings, numbers and booleans are allowed (no nesting).')
    return out


def validate_snapshot(body):
    """-> (profile, context) cleaned dicts. Unknown top-level keys are refused (a `sensitive` section gets its own code)."""
    if not isinstance(body, dict):
        _bad('The body must be a JSON object.')
    if 'sensitive' in body:
        _bad('Sensitive context is not accepted by Contract v1.', 'sensitive_context_not_accepted')
    unknown = sorted(set(body) - set(SECTIONS))
    if unknown:
        _bad(f'Unknown section(s): {", ".join(unknown)}. Allowed: {", ".join(SECTIONS)}.')
    profile = _clean_section('profile', body.get('profile', {}))
    context = _clean_section('context', body.get('context', {}))
    if len(json.dumps({'profile': profile, 'context': context}, ensure_ascii=False).encode()) > MAX_TOTAL_BYTES:
        _bad(f'The snapshot may not exceed {MAX_TOTAL_BYTES} bytes.', 'context_too_large')
    return profile, context


@transaction.atomic
def store(identity, key, profile, context):
    """Replace the whole snapshot of one verified customer (idempotent). Returns the saved row."""
    row, _ = ExternalContext.objects.update_or_create(
        identity=identity, defaults={'profile': profile, 'context': context, 'source_kid': key.kid})
    return row


def for_visitor(visitor):
    """What the operator panel shows for a conversation's visitor, or None (guests and unverified ids have none)."""
    identity = getattr(visitor, 'external_identity', None)
    row = getattr(identity, 'host_context', None) if identity is not None else None
    if row is None:
        return None
    return {'profile': row.profile, 'context': row.context, 'updated_at': row.updated_at}
