"""Audit trail for integration operations, written to the existing `audit.AuditEvent` table (no second audit store).

Actor is None (the caller is an integration, not a RastiChat user); the integration and key are recorded in the
metadata. Metadata never contains tokens, keys, request bodies or PII — identifiers and field *names* only.
"""
from audit.models import AuditEvent

_FORBIDDEN_META_KEYS = {'token', 'secret', 'password', 'private_key', 'authorization'}


def record(action, *, integration=None, key=None, target_type='integration', target_id='', actor=None, **meta):
    clean = {k: v for k, v in meta.items() if k.lower() not in _FORBIDDEN_META_KEYS}
    if integration is not None:
        clean['integration'] = integration.slug
    if key is not None:
        clean['kid'] = key.kid
    return AuditEvent.objects.create(
        actor=actor, action=f'integration.{action}', target_type=target_type, target_id=str(target_id), metadata=clean,
    )
