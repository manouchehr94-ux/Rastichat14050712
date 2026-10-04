from .models import OperatorPresence


def touch_presence(user, explicit_status=None):
    """Record operator activity, optionally setting an explicit status.

    Connecting to the dashboard WS or sending a message just bumps
    `last_active_at` without touching an explicit status the operator chose
    (e.g. deliberately going OFFLINE) — only the presence-set endpoint passes
    `explicit_status`. A first-ever touch (no row yet) defaults to ONLINE,
    since there is no explicit choice to respect at that point.
    """
    presence, created = OperatorPresence.objects.get_or_create(
        user=user, defaults={'status': OperatorPresence.Status.ONLINE},
    )
    if explicit_status:
        presence.status = explicit_status
        presence.save(update_fields=['status', 'last_active_at'])
    elif not created:
        presence.save(update_fields=['last_active_at'])
    return presence
