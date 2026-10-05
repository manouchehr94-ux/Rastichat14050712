from django.conf import settings
from django.core.management.base import BaseCommand

from common import legacy_credentials


class Command(BaseCommand):
    help = (
        'Read-only: how many legacy credentials-in-URL connections/requests were accepted per day, surface and '
        'project (Redis counters). Use it before the exception window ends to see which projects still embed an '
        'old widget bundle. Writes nothing.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)

    def handle(self, *args, **options):
        usage = legacy_credentials.read_usage(max(1, options['days']))
        until = getattr(settings, 'LEGACY_URL_CREDENTIALS_UNTIL', None)
        self.stdout.write(f'exception window ends: {until or "not configured"}')
        if not usage:
            self.stdout.write('no legacy credential use recorded in the period')
            return
        totals = {}
        for day in sorted(usage, reverse=True):
            self.stdout.write(str(day))
            for (surface, project), count in sorted(usage[day].items()):
                self.stdout.write(f'  {surface:12} project={project:36} {count}')
                totals[project] = totals.get(project, 0) + count
        self.stdout.write('projects still using a legacy widget (period total):')
        for project, count in sorted(totals.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {project}: {count}')
