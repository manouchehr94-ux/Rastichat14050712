from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.models import IdempotencyRecord


class Command(BaseCommand):
    help = 'Delete idempotency records older than N hours (default 48). Safe to run from cron.'

    def add_arguments(self, parser):
        parser.add_argument('--older-than-hours', type=int, default=48)

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(hours=opts['older_than_hours'])
        deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(f'Deleted {deleted} idempotency record(s).')
