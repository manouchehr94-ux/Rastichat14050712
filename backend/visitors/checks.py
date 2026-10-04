from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def unverified_external_id_check(app_configs, **kwargs):
    """Flag the spoofable legacy widget identity lookup in `check --deploy`.

    CI and the deploy scripts run `check --deploy --fail-level WARNING --tag
    security`, so a deployment with WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID on can
    never pass that gate unnoticed.
    """
    if not getattr(settings, 'WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID', False):
        return []
    return [Warning(
        'WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID is enabled: the public widget init endpoint trusts a browser-supplied '
        'external_id, so any caller who knows or guesses it can read that customer\'s conversation.',
        hint='Unset it once the embedding site uses signed identity (docs/runbooks/WIDGET_IDENTITY_MIGRATION.md).',
        id='visitors.W001',
    )]
