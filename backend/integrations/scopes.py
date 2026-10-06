"""Scopes an integration (and each of its signing keys) may hold.

Least privilege: an `Integration` lists the scopes it is *allowed* to use, each `IntegrationKey` lists the subset
that key may exercise, and a request is authorised only for the intersection. A key used by an identity endpoint
therefore cannot provision tenants even though the integration as a whole may.

Scopes are generic capabilities of the Integration Contract — never host-specific.
"""

TENANTS_READ = 'tenants:read'
TENANTS_WRITE = 'tenants:write'
# Reserved for the identity / SSO, platform-initiation and event slices of Contract v1. Declared now so a key can be
# issued with the final scope vocabulary and the contract does not change shape when those slices land.
IDENTITY_CUSTOMER = 'identity:customer'
IDENTITY_STAFF = 'identity:staff'
IDENTITY_PLATFORM = 'identity:platform'
CONVERSATIONS_INITIATE = 'conversations:initiate'
EVENTS_RECEIVE = 'events:receive'

ALL_SCOPES = (
    TENANTS_READ, TENANTS_WRITE,
    IDENTITY_CUSTOMER, IDENTITY_STAFF, IDENTITY_PLATFORM,
    CONVERSATIONS_INITIATE, EVENTS_RECEIVE,
)


def validate_scopes(value):
    from django.core.exceptions import ValidationError
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        raise ValidationError('scopes must be a list of strings.')
    unknown = sorted(set(value) - set(ALL_SCOPES))
    if unknown:
        raise ValidationError(f'Unknown scope(s): {", ".join(unknown)}')
