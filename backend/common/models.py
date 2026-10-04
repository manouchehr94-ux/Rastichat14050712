from django.db import models


class SchedulerHeartbeat(models.Model):
    """One row per background scheduler (automation-worker, sla-worker),
    updated at the end of every cycle by `manage.py record_scheduler_heartbeat`
    (called from docker-scheduler-loop.sh). Deliberately DB-backed rather
    than only the container-local heartbeat file the Docker HEALTHCHECK
    reads (see docker-scheduler-loop.sh) — a file living inside the worker
    container's own filesystem is invisible to the backend process, so
    there would be no way for common.views.SchedulerStatusView (polled by
    external monitoring, not Docker itself) to answer "when did the
    automation worker last run" without a shared source of truth.
    """

    class Status(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILURE = 'FAILURE', 'Failure'

    name = models.CharField(max_length=100, unique=True)
    last_run_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices)
    detail = models.CharField(max_length=500, blank=True, default='')

    def __str__(self):
        return f'{self.name}: {self.status} at {self.last_run_at.isoformat()}'
