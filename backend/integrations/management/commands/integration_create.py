from django.core.management.base import BaseCommand, CommandError

from integrations import audit
from integrations.models import Integration
from integrations.scopes import ALL_SCOPES
from platforms.models import Platform


class Command(BaseCommand):
    help = 'Register a host application as an Integration (idempotent on --slug is NOT assumed: an existing slug is an error).'

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True)
        parser.add_argument('--name', required=True)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('--platform-id', type=int)
        group.add_argument('--platform-external-id')
        parser.add_argument('--scopes', default='tenants:read,tenants:write',
                            help=f'comma separated; available: {", ".join(ALL_SCOPES)}')

    def handle(self, *args, **opts):
        platform = (Platform.objects.filter(pk=opts['platform_id']) if opts['platform_id']
                    else Platform.objects.filter(external_id=opts['platform_external_id'])).first()
        if platform is None:
            raise CommandError('Platform not found.')
        if Integration.objects.filter(slug=opts['slug']).exists():
            raise CommandError(f'Integration "{opts["slug"]}" already exists.')
        integration = Integration(
            slug=opts['slug'], name=opts['name'], platform=platform,
            scopes=[s.strip() for s in opts['scopes'].split(',') if s.strip()],
        )
        integration.full_clean()
        integration.save()
        audit.record('created', integration=integration, target_id=integration.pk)
        self.stdout.write(self.style.SUCCESS(f'Integration "{integration.slug}" created (platform {platform.pk}). '
                                             'Next: integration_key_add with the host\'s PUBLIC key.'))
