from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations import audit
from integrations.models import IntegrationKey


class Command(BaseCommand):
    help = 'Revoke a signing key. Effective immediately: tokens signed by it are refused from the next request.'

    def add_arguments(self, parser):
        parser.add_argument('--kid', required=True)
        parser.add_argument('--reason', default='')

    def handle(self, *args, **opts):
        key = IntegrationKey.objects.select_related('integration').filter(kid=opts['kid']).first()
        if key is None:
            raise CommandError('Key not found.')
        if key.status == IntegrationKey.Status.REVOKED:
            self.stdout.write('Already revoked.')
            return
        key.status = IntegrationKey.Status.REVOKED
        key.revoked_at = timezone.now()
        key.revoked_reason = opts['reason'][:255]
        key.save(update_fields=['status', 'revoked_at', 'revoked_reason'])
        audit.record('key_revoked', integration=key.integration, key=key, target_type='integration_key',
                     target_id=key.pk, reason=key.revoked_reason)
        self.stdout.write(self.style.SUCCESS(f'Revoked {key.kid}.'))
