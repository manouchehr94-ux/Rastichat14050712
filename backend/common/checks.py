from django.conf import settings
from django.core.checks import Info, Tags, Warning, register

from common import legacy_credentials


@register(Tags.security, deploy=True)
def legacy_url_credentials_check(app_configs, **kwargs):
    """`check --deploy --tag security` (the CI/deploy gate) and the legacy credentials-in-URL exception.

    * Nothing configured on staging/production: no message — the gate is green and legacy is off.
    * An owner-approved exception inside its window: an INFO line (visible in every gate run and
      deploy log; it does not fail `--fail-level WARNING`) stating scope and end date. The window is
      validated at startup and enforced on every connection, so the exception cannot outlive its date.
    * The exception variables left behind after the window (or a process whose window lapsed): a WARNING,
      i.e. the gate is red until they are removed.
    """
    if not getattr(settings, 'IS_PRODUCTION_LIKE', False):
        return []
    expired_on = getattr(settings, 'LEGACY_URL_CREDENTIALS_EXPIRED_ON', None)
    if expired_on is not None:
        return [Warning(
            f'The approved legacy credentials-in-URL exception ended on {expired_on}; legacy is OFF but its '
            'environment variables are still set.',
            hint='Remove LEGACY_URL_CREDENTIALS_ENABLED / _ACK / _SCOPE / _UNTIL '
                 '(docs/runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md).',
            id='common.W003',
        )]
    if not getattr(settings, 'LEGACY_URL_CREDENTIALS_ENABLED', False):
        return []
    until = getattr(settings, 'LEGACY_URL_CREDENTIALS_UNTIL', None)
    if until is None or legacy_credentials.utc_today() > until:
        return [Warning(
            'LEGACY_URL_CREDENTIALS_ENABLED is on without a valid, unexpired window.',
            hint='Remove the legacy variables (docs/runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md).',
            id='common.W002',
        )]
    return [Info(
        f'TEMPORARY EXCEPTION ACTIVE: visitor session tokens (widget only) are accepted in WebSocket/query-string '
        f'URLs until {until} (UTC). Staff JWTs in URLs stay refused.',
        hint='Owner-approved and time-boxed; track usage with `manage.py report_legacy_credential_usage`.',
        id='common.I002',
    )]
