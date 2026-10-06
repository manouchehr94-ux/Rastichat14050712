from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations import audit
from integrations.models import Integration


class Command(BaseCommand):
    help = 'Disable or re-enable an integration (a disabled integration is refused on every endpoint; tenants/history are kept).'

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--disable', action='store_true')
        group.add_argument('--enable', action='store_true')

    def handle(self, *args, **opts):
        integration = Integration.objects.filter(slug=opts['slug']).first()
        if integration is None:
            raise CommandError('Integration not found.')
        active = bool(opts['enable'])
        if integration.is_active == active:
            self.stdout.write('No change.')
            return
        integration.is_active = active
        integration.disabled_at = None if active else timezone.now()
        integration.save(update_fields=['is_active', 'disabled_at', 'updated_at'])
        audit.record('enabled' if active else 'disabled', integration=integration, target_id=integration.pk)
        self.stdout.write(self.style.SUCCESS(f'{integration.slug}: {"enabled" if active else "disabled"}.'))
