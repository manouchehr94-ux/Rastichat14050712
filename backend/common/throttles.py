from rest_framework.throttling import UserRateThrottle


class StaffWriteThrottle(UserRateThrottle):
    """Per-authenticated-user limit on WRITE actions of the staff-facing support APIs (creating a
    ticket, sending/replying, assigning, marking read) — a compromised or buggy client must not be
    able to flood a store's or the platform's support inbox. Disabled under `manage.py test` like
    every other scope (see settings.TESTING)."""
    scope = 'support_write'
