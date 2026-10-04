from datetime import timedelta

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

import visitors.models


def backfill_legacy_expiry(apps, schema_editor):
    """Legacy sessions have no expiry. Give them a one-TTL grace period from the
    moment of deployment (so a customer with an open conversation keeps working
    and renews by simply using the widget) — but never leave them valid forever.
    Only `expires_at` is written: no Visitor, Conversation or Message is touched.
    """
    VisitorSession = apps.get_model('visitors', 'VisitorSession')
    now = timezone.now()
    VisitorSession.objects.filter(expires_at__isnull=True).update(
        expires_at=now + timedelta(days=settings.VISITOR_SESSION_TTL_DAYS),
        # Their absolute lifetime also starts counting at deployment: measuring it from the
        # (possibly years-old) created_at would invalidate every legacy customer instantly.
        hard_expires_at=now + timedelta(days=settings.VISITOR_SESSION_MAX_AGE_DAYS),
    )
    VisitorSession.objects.filter(hard_expires_at__isnull=True).update(
        hard_expires_at=now + timedelta(days=settings.VISITOR_SESSION_MAX_AGE_DAYS),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('visitors', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='visitorsession',
            name='revoked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='visitorsession',
            name='hard_expires_at',
            field=models.DateTimeField(blank=True, null=True, default=visitors.models.default_session_hard_expiry),
        ),
        migrations.AddField(
            model_name='visitorsession',
            name='rotated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='visitorsession',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True, default=visitors.models.default_session_expiry),
        ),
        migrations.RunPython(backfill_legacy_expiry, migrations.RunPython.noop),
    ]
