"""Confirms the real domain services actually publish the events they claim
to (spec section 16) — using an active AutomationRule as the observable
proof, rather than inspecting publish_event call sites directly. Each test
exercises the real integration point (an HTTP endpoint, a WebSocket
consumer, or a domain service function) rather than calling the automation
engine directly, since tests_engine.py already covers the engine in
isolation.

HTTP-endpoint tests need captureOnCommitCallbacks(execute=True): publish_event
defers automation processing to transaction.on_commit, and Django's TestCase
wraps each test in a rolled-back transaction where a real commit — and thus
a real on_commit firing — never happens.
"""
from channels.testing import WebsocketCommunicator
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from config.asgi import application
from conversations import services as conv_services
from conversations.models import Conversation, Message
from teams.models import Team
from visitors.models import VisitorSession
from .models import AutomationRule
from .tests_base import AutomationTestMixin


class DomainServiceIntegrationTests(TestCase, AutomationTestMixin):
    """Each of these calls a real domain service function directly (not
    process_event) and checks that automation actually ran, proving the
    publish_event() wiring added to that service is live.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()
        self.tag_target = self.make_operator(self.ws)

    def _rule(self, trigger_type, **kwargs):
        defaults = dict(workspace=self.ws, is_active=True, conditions={}, name=f'auto-{trigger_type}')
        defaults.update(kwargs)
        return AutomationRule.objects.create(trigger_type=trigger_type, **defaults)

    def test_assignment_triggers_automation(self):
        self._rule(AutomationRule.Trigger.CONVERSATION_ASSIGNED, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.assign_to_agent(self.conv, actor=None, target_user_id=self.tag_target.id, reason='test')
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')

    def test_unassign_triggers_automation(self):
        self.conv.assigned_to = self.tag_target
        self.conv.save(update_fields=['assigned_to'])
        self._rule(AutomationRule.Trigger.CONVERSATION_UNASSIGNED, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.unassign(self.conv, actor=None, reason='test')
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')

    def test_transfer_triggers_automation(self):
        team = Team.objects.create(workspace=self.ws, name='Sales')
        self._rule(AutomationRule.Trigger.CONVERSATION_TRANSFERRED, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.transfer_team(self.conv, actor=None, new_team_id=team.id, reason='test')
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')

    def test_escalate_triggers_automation(self):
        self._rule(AutomationRule.Trigger.CONVERSATION_ESCALATED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'auto note'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.escalate(self.conv, actor=None, reason='test')
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

    def test_priority_change_triggers_automation(self):
        self._rule(AutomationRule.Trigger.CONVERSATION_PRIORITY_CHANGED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'priority changed'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.set_priority(self.conv, actor=None, priority='URGENT', reason='test')
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

    def test_close_triggers_automation(self):
        self._rule(AutomationRule.Trigger.CONVERSATION_CLOSED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'closed'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.close_conversation(self.conv, actor=None)
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

    def test_reopen_triggers_automation(self):
        conv_services.close_conversation(self.conv, actor=None)
        self._rule(AutomationRule.Trigger.CONVERSATION_REOPENED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'reopened'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.reopen_conversation(self.conv, actor=None)
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

    def test_status_change_triggers_automation(self):
        """CONVERSATION_STATUS_CHANGED had zero publishers before set_status()
        (added alongside the SET_STATUS action fix) — this is the first real
        wiring of that trigger type.
        """
        self._rule(AutomationRule.Trigger.CONVERSATION_STATUS_CHANGED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'status changed'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.set_status(self.conv, actor=None, status='PENDING')
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

    def test_queue_entry_triggers_automation(self):
        self._rule(AutomationRule.Trigger.CONVERSATION_QUEUED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'queued'}}])
        with self.captureOnCommitCallbacks(execute=True):
            conv_services.return_to_queue(self.conv, actor=None, reason='test')
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())

    def test_sla_breach_triggers_automation(self):
        from sla.models import ConversationSLA
        self._rule(AutomationRule.Trigger.SLA_BREACHED, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'sla breached'}}])
        ConversationSLA.objects.create(
            conversation=self.conv, first_response_target_minutes=30,
            first_response_due_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        with self.captureOnCommitCallbacks(execute=True):
            call_command('evaluate_sla')
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())


class HttpEndpointIntegrationTests(TestCase, AutomationTestMixin):
    def setUp(self):
        self.client = APIClient()
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()
        self.operator = self.make_operator(self.ws)

    def test_widget_start_triggers_conversation_created_automation(self):
        AutomationRule.objects.create(
            workspace=self.ws, name='on create', is_active=True, trigger_type=AutomationRule.Trigger.CONVERSATION_CREATED,
            conditions={}, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
        )
        session = VisitorSession.objects.create(visitor=self.visitor)
        Conversation.objects.filter(pk=self.conv.pk).delete()  # start fresh so widget/start creates a brand-new conversation

        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/v1/widget/start/', {'session_token': str(session.token)}, format='json')
        self.assertEqual(res.status_code, 200)
        conv = Conversation.objects.get(id=res.data['id'])
        self.assertEqual(conv.priority, 'HIGH')

    def test_conversation_created_sees_final_queue_team_sla_state(self):
        """Regression for the CONVERSATION_CREATED ordering bug: routing and
        SLA application must be persisted BEFORE the event is published, so
        a condition on conversation.queue_id/team_id/sla_state sees the
        conversation's real, final creation-time state — not an unrouted,
        SLA-less intermediate one.
        """
        from teams.models import Team
        from queues.models import Queue
        from sla.models import SLAPolicy

        team = Team.objects.create(workspace=self.ws, name='Sales')
        queue = Queue.objects.create(workspace=self.ws, team=team, name='Main Queue')
        SLAPolicy.objects.create(
            workspace=self.ws, name='Default', first_response_target_minutes=30,
            next_response_target_minutes=60, resolution_target_minutes=240,
        )
        AutomationRule.objects.create(
            workspace=self.ws, name='sees routed state', is_active=True, trigger_type=AutomationRule.Trigger.CONVERSATION_CREATED,
            conditions={'field': 'conversation.queue_id', 'operator': 'exists'},
            actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
        )
        session = VisitorSession.objects.create(visitor=self.visitor)
        Conversation.objects.filter(pk=self.conv.pk).delete()

        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/v1/widget/start/', {'session_token': str(session.token)}, format='json')
        self.assertEqual(res.status_code, 200)
        conv = Conversation.objects.get(id=res.data['id'])
        self.assertEqual(conv.queue_id, queue.id)
        self.assertEqual(conv.team_id, team.id)
        self.assertTrue(hasattr(conv, 'sla'))
        # The condition on conversation.queue_id must have matched at trigger
        # time (not just be true "by the time we check now") — the action it
        # gated on only runs if the engine saw queue_id already set.
        self.assertEqual(conv.priority, 'HIGH')

    def test_resumed_existing_conversation_does_not_republish_created_event(self):
        """A visitor re-opening an already-open conversation must not look
        like a second CONVERSATION_CREATED."""
        AutomationRule.objects.create(
            workspace=self.ws, name='on create', is_active=True, trigger_type=AutomationRule.Trigger.CONVERSATION_CREATED,
            conditions={}, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'created'}}],
        )
        session = VisitorSession.objects.create(visitor=self.visitor)
        Conversation.objects.filter(pk=self.conv.pk).delete()  # start fresh so the first call actually creates one
        with self.captureOnCommitCallbacks(execute=True):
            res1 = self.client.post('/api/v1/widget/start/', {'session_token': str(session.token)}, format='json')
        self.assertEqual(res1.status_code, 200)
        first_conv_id = res1.data['id']

        with self.captureOnCommitCallbacks(execute=True):
            res2 = self.client.post('/api/v1/widget/start/', {'session_token': str(session.token)}, format='json')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data['id'], first_conv_id)  # same conversation, resumed not recreated

        note_count = Message.objects.filter(
            conversation_id=first_conv_id, message_type=Message.MessageType.INTERNAL_NOTE,
        ).count()
        self.assertEqual(note_count, 1)  # exactly one CONVERSATION_CREATED, from the first call only

    def test_operator_send_message_triggers_automation(self):
        AutomationRule.objects.create(
            workspace=self.ws, name='on op message', is_active=True, trigger_type=AutomationRule.Trigger.OPERATOR_MESSAGE_CREATED,
            conditions={}, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
        )
        self.login(self.client, self.operator)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(f'/api/v1/conversations/{self.conv.id}/send/', {'content': 'hi there', 'client_message_id': 'op-1'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')

    def test_internal_note_creation_triggers_automation(self):
        AutomationRule.objects.create(
            workspace=self.ws, name='on note', is_active=True, trigger_type=AutomationRule.Trigger.INTERNAL_NOTE_CREATED,
            conditions={}, actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
        )
        self.login(self.client, self.operator)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                f'/api/v1/conversations/customer/{self.conv.id}/internal_notes/',
                {'content': 'internal note', 'client_message_id': 'note-1'}, format='json',
            )
        self.assertEqual(res.status_code, 201, res.data)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.priority, 'HIGH')

    def test_rating_submission_triggers_automation(self):
        AutomationRule.objects.create(
            workspace=self.ws, name='on rating', is_active=True, trigger_type=AutomationRule.Trigger.RATING_SUBMITTED,
            conditions={}, actions=[{'type': 'CREATE_INTERNAL_NOTE', 'params': {'content': 'thanks for rating'}}],
        )
        session = VisitorSession.objects.create(visitor=self.visitor)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                f'/api/v1/widget/conversations/{self.conv.id}/rate/',
                {'session_token': str(session.token), 'rating': 5}, format='json',
            )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(Message.objects.filter(conversation=self.conv, message_type=Message.MessageType.INTERNAL_NOTE).exists())


class WebsocketIntegrationTests(TransactionTestCase, AutomationTestMixin):
    """Real commits happen in a TransactionTestCase, so on_commit fires
    naturally here — no captureOnCommitCallbacks needed.
    """

    def setUp(self):
        self.ws, self.project, self.visitor, self.conv = self.make_full_stack()

    async def test_customer_message_via_widget_socket_triggers_automation(self):
        from channels.db import database_sync_to_async

        await database_sync_to_async(AutomationRule.objects.create)(
            workspace=self.ws, name='on customer msg', is_active=True,
            trigger_type=AutomationRule.Trigger.CUSTOMER_MESSAGE_CREATED, conditions={},
            actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
        )
        session = await database_sync_to_async(VisitorSession.objects.create)(visitor=self.visitor)
        communicator = WebsocketCommunicator(application, f"/ws/widget/{session.token}/{self.conv.id}/")
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.send_json_to({'message': 'hello there', 'client_message_id': 'cm-1'})
        await communicator.receive_json_from(timeout=5)
        import asyncio
        await asyncio.sleep(0.3)  # give the automation engine's synchronous post-commit processing a moment
        await communicator.disconnect()

        refreshed = await database_sync_to_async(Conversation.objects.get)(pk=self.conv.pk)
        self.assertEqual(refreshed.priority, 'HIGH')
