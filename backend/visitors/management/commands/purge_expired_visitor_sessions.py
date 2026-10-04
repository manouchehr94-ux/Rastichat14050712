from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from visitors.models import VisitorSession


class Command(BaseCommand):
    help = (
        'Delete visitor SESSION rows that expired or were revoked more than --older-than-days ago. '
        'Only credentials are removed: Visitors, Conversations and Messages are never touched.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--older-than-days', type=int, default=30)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['older_than_days'])
        qs = VisitorSession.objects.filter(Q(expires_at__lt=cutoff) | Q(revoked_at__lt=cutoff))
        count = qs.count()
        if options['dry_run']:
            self.stdout.write(f'[dry-run] {count} session(s) would be deleted')
            return
        qs.delete()
        self.stdout.write(f'Deleted {count} session(s)')
