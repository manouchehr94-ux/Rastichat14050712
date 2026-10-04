from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from django.test import TestCase
from django.core.management import call_command
from django.utils import timezone
from io import StringIO

from accounts.models import User
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from projects.models import Project
from visitors.models import Visitor
from conversations.models import Conversation
from audit.models import AuditEvent
from notifications.models import Notification
from .models import BusinessCalendar, WorkingInterval, Holiday, SLAPolicy, ConversationSLA
from .services import add_business_minutes, apply_sla, select_policy


class BusinessHoursCalculationTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.calendar = BusinessCalendar.objects.create(workspace=self.workspace, name='Standard', timezone='UTC')
        for weekday in range(5):  # Monday-Friday
            WorkingInterval.objects.create(calendar=self.calendar, weekday=weekday, start_time=time(9, 0), end_time=time(17, 0))

    def test_24x7_target_with_no_calendar_is_plain_wall_clock(self):
        start = timezone.now()
        due = add_business_minutes(start, 30, None)
        self.assertEqual(due, start + timedelta(minutes=30))

    def test_deadline_within_a_single_working_window(self):
        # Monday 10:00 UTC + 30 minutes, well inside the 09:00-17:00 window.
        start = datetime(2025, 1, 6, 10, 0, tzinfo=ZoneInfo('UTC'))  # a Monday
        due = add_business_minutes(start, 30, self.calendar)
        self.assertEqual(due, start + timedelta(minutes=30))

    def test_deadline_spills_into_the_next_working_window(self):
        # Monday 16:50 + 30 minutes: 10 minutes left today, 20 minutes carry
        # over to Tuesday 09:00 -> Tuesday 09:20.
        start = datetime(2025, 1, 6, 16, 50, tzinfo=ZoneInfo('UTC'))
        due = add_business_minutes(start, 30, self.calendar)
        self.assertEqual(due, datetime(2025, 1, 7, 9, 20, tzinfo=ZoneInfo('UTC')))

    def test_weekend_is_skipped(self):
        # Friday 16:50 + 30 minutes must land Monday morning, not Saturday.
        start = datetime(2025, 1, 10, 16, 50, tzinfo=ZoneInfo('UTC'))  # a Friday
        due = add_business_minutes(start, 30, self.calendar)
        self.assertEqual(due.weekday(), 0)  # Monday
        self.assertEqual(due, datetime(2025, 1, 13, 9, 20, tzinfo=ZoneInfo('UTC')))

    def test_holiday_is_excluded(self):
        # Make Tuesday a holiday; a Monday-16:50 deadline should now skip to Wednesday.
        Holiday.objects.create(calendar=self.calendar, date=datetime(2025, 1, 7).date(), name='Public holiday')
        start = datetime(2025, 1, 6, 16, 50, tzinfo=ZoneInfo('UTC'))
        due = add_business_minutes(start, 30, self.calendar)
        self.assertEqual(due.date(), datetime(2025, 1, 8).date())  # Wednesday

    def test_exceptional_working_day_opens_a_normally_closed_saturday(self):
        # Saturday is normally closed (no WorkingInterval for weekday=5).
        # Mark it an exceptional working day with its own 10:00-14:00 hours
        # and confirm a Friday-evening deadline now lands there instead of
        # skipping all the way to Monday.
        Holiday.objects.create(
            calendar=self.calendar, date=datetime(2025, 1, 11).date(), is_working=True,  # Saturday
            start_time=time(10, 0), end_time=time(14, 0),
        )
        start = datetime(2025, 1, 10, 16, 50, tzinfo=ZoneInfo('UTC'))  # Friday, 10 min left today
        due = add_business_minutes(start, 30, self.calendar)  # 10 today + 20 into Saturday's exceptional window
        self.assertEqual(due, datetime(2025, 1, 11, 10, 20, tzinfo=ZoneInfo('UTC')))

    def test_conversation_created_outside_business_hours_starts_from_next_open(self):
        # Monday 22:00 (after hours) + 15 minutes -> Tuesday 09:15.
        start = datetime(2025, 1, 6, 22, 0, tzinfo=ZoneInfo('UTC'))
        due = add_business_minutes(start, 15, self.calendar)
        self.assertEqual(due, datetime(2025, 1, 7, 9, 15, tzinfo=ZoneInfo('UTC')))

    def test_dst_transition_does_not_crash_and_stays_bounded(self):
        # US DST spring-forward 2025-03-09. A calendar open nearly all day
        # should compute without error and land the same day.
        dst_calendar = BusinessCalendar.objects.create(workspace=self.workspace, name='DST', timezone='America/New_York')
        for weekday in range(7):
            WorkingInterval.objects.create(calendar=dst_calendar, weekday=weekday, start_time=time(0, 0), end_time=time(23, 59))
        start = datetime(2025, 3, 9, 1, 0, tzinfo=ZoneInfo('America/New_York'))
        due = add_business_minutes(start, 30, dst_calendar)
        self.assertEqual(due.date(), start.date())


class SLAPolicySelectionAndApplicationTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.visitor = Visitor.objects.create(project=self.project)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, priority=Conversation.Priority.URGENT,
        )

    def test_most_specific_policy_wins(self):
        SLAPolicy.objects.create(workspace=self.workspace, name='Blanket', first_response_target_minutes=60, resolution_target_minutes=480)
        specific = SLAPolicy.objects.create(
            workspace=self.workspace, name='Urgent', priority=Conversation.Priority.URGENT,
            first_response_target_minutes=5, resolution_target_minutes=30,
        )
        chosen = select_policy(self.conv)
        self.assertEqual(chosen.id, specific.id)

    def test_apply_sla_persists_a_policy_snapshot_and_deadlines(self):
        SLAPolicy.objects.create(
            workspace=self.workspace, name='Urgent', priority=Conversation.Priority.URGENT,
            first_response_target_minutes=10, resolution_target_minutes=60,
        )
        sla = apply_sla(self.conv)
        self.assertIsNotNone(sla)
        self.assertEqual(sla.policy_name_snapshot, 'Urgent')
        self.assertIsNotNone(sla.first_response_due_at)
        self.assertIsNotNone(sla.resolution_due_at)


class SLAEvaluatorIdempotencyTests(TestCase):
    def setUp(self):
        self.platform = Platform.objects.create(name='P1')
        self.workspace = Workspace.objects.create(name='WS1', platform=self.platform)
        self.project = Project.objects.create(name='Store', workspace=self.workspace)
        self.operator = User.objects.create_user(email='op@test.com', password='pass1234')
        WorkspaceMembership.objects.create(user=self.operator, workspace=self.workspace, role='WORKSPACE_OPERATOR')
        self.visitor = Visitor.objects.create(project=self.project)
        self.conv = Conversation.objects.create(
            visitor=self.visitor, workspace=self.workspace, type=Conversation.Type.CUSTOMER,
            status=Conversation.Status.OPEN, assigned_to=self.operator,
        )
        policy = SLAPolicy.objects.create(workspace=self.workspace, name='Fast', first_response_target_minutes=10, resolution_target_minutes=60)
        self.sla = ConversationSLA.objects.create(
            conversation=self.conv, policy=policy, policy_name_snapshot='Fast',
            first_response_target_minutes=10, resolution_target_minutes=60,
            first_response_due_at=timezone.now() - timedelta(minutes=1),  # already breached
            resolution_due_at=timezone.now() + timedelta(hours=1),
        )

    def _run(self):
        out = StringIO()
        call_command('evaluate_sla', stdout=out)
        return out.getvalue()

    def test_breach_is_recorded_once_even_across_two_runs(self):
        self._run()
        self.sla.refresh_from_db()
        self.assertIsNotNone(self.sla.first_response_breached_at)
        first_breach_at = self.sla.first_response_breached_at
        breach_events = AuditEvent.objects.filter(action='sla_breached')
        self.assertEqual(breach_events.count(), 1)
        breach_notifs = Notification.objects.filter(event_type=Notification.EventType.SLA_BREACHED)
        self.assertEqual(breach_notifs.count(), 1)

        self._run()  # second run must not duplicate anything
        self.sla.refresh_from_db()
        self.assertEqual(self.sla.first_response_breached_at, first_breach_at)
        self.assertEqual(AuditEvent.objects.filter(action='sla_breached').count(), 1)
        self.assertEqual(Notification.objects.filter(event_type=Notification.EventType.SLA_BREACHED).count(), 1)

    def test_approaching_notification_is_not_duplicated(self):
        self.sla.first_response_due_at = timezone.now() + timedelta(minutes=2)
        self.sla.save(update_fields=['first_response_due_at'])

        self._run()
        self.assertEqual(AuditEvent.objects.filter(action='sla_approaching').count(), 1)
        self.assertEqual(Notification.objects.filter(event_type=Notification.EventType.SLA_APPROACHING).count(), 1)

        self._run()
        self.assertEqual(AuditEvent.objects.filter(action='sla_approaching').count(), 1)
        self.assertEqual(Notification.objects.filter(event_type=Notification.EventType.SLA_APPROACHING).count(), 1)

    def test_resolution_breach_escalates_conversation(self):
        self.sla.first_response_due_at = timezone.now() + timedelta(hours=1)
        self.sla.resolution_due_at = timezone.now() - timedelta(minutes=1)
        self.sla.save(update_fields=['first_response_due_at', 'resolution_due_at'])

        self._run()
        self.sla.refresh_from_db()
        self.conv.refresh_from_db()
        self.assertIsNotNone(self.sla.resolution_breached_at)
        self.assertEqual(self.conv.priority, Conversation.Priority.URGENT)
