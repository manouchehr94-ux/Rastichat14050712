from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def legacy_url_credentials_check(app_configs, **kwargs):
    """`check --deploy --tag security` (the CI/deploy gate) fails while credentials-in-URL is on
    in a production-like environment (it can only be on there with an explicit acknowledgement)."""
    if not (getattr(settings, 'IS_PRODUCTION_LIKE', False) and getattr(settings, 'LEGACY_URL_CREDENTIALS_ENABLED', False)):
        return []
    return [Warning(
        'LEGACY_URL_CREDENTIALS_ENABLED is on: JWTs / visitor session tokens may travel in WebSocket and query-string '
        'URLs and will be recorded by reverse-proxy access logs.',
        hint='Finish rolling out ticket-based clients, then unset it (docs/runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md).',
        id='common.W002',
    )]
