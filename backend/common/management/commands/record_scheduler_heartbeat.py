"""Called by docker-scheduler-loop.sh at the end of every cycle — updates
the DB-backed heartbeat row that common.views.SchedulerStatusView reports
on (see common/models.py:SchedulerHeartbeat for why this is DB-backed
rather than only the loop's own local heartbeat file).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from common.models import SchedulerHeartbeat


class Command(BaseCommand):
    help = 'Record that a scheduler (automation-worker, sla-worker) completed a cycle.'

    def add_arguments(self, parser):
        parser.add_argument('name', help='Scheduler name, e.g. automation-worker or sla-worker')
        parser.add_argument('status', choices=['SUCCESS', 'FAILURE'])
        parser.add_argument('--detail', default='')

    def handle(self, *args, **options):
        SchedulerHeartbeat.objects.update_or_create(
            name=options['name'],
            defaults={
                'last_run_at': timezone.now(),
                'status': options['status'],
                'detail': options['detail'][:500],
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Recorded heartbeat for '{options['name']}': {options['status']}"))
