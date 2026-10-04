"""Concurrency-safe queue routing and claim/auto-assignment.

Every mutation here locks the target Conversation row with
`select_for_update()` inside a transaction, so two concurrent attempts to
claim or auto-assign the same conversation always serialize: the loser's
transaction blocks until the winner commits, then re-reads the now-assigned
row and fails cleanly instead of overwriting the winner.
"""
import random
from django.db import transaction

from accounts.models import OperatorPresence
from accounts.presence import touch_presence
from teams.models import TeamMembership
from automations.events import publish_event
from .models import Queue


class AssignmentError(Exception):
    pass


def _eligible_agents(queue):
    """Active team members currently eligible for automatic assignment:
    active membership, active user account, not OFFLINE/DO_NOT_DISTURB, and
    under whichever capacity applies (the queue's override, else the
    agent's own configured max_capacity).
    """
    memberships = TeamMembership.objects.filter(
        team=queue.team, is_active=True, user__is_active=True,
    ).select_related('user')
    eligible = []
    for membership in memberships:
        presence, _ = OperatorPresence.objects.get_or_create(
            user=membership.user, defaults={'status': OperatorPresence.Status.OFFLINE},
        )
        status = presence.effective_status()
        if status in (OperatorPresence.Status.OFFLINE, OperatorPresence.Status.DO_NOT_DISTURB):
            continue
        capacity = queue.max_active_per_agent or presence.max_capacity
        if presence.is_at_capacity(capacity):
            continue
        eligible.append((membership.user, presence))
    return eligible


def _pick_agent(queue, eligible):
    if queue.assignment_strategy == Queue.Strategy.RANDOM:
        return random.choice(eligible)[0]
    if queue.assignment_strategy == Queue.Strategy.LEAST_ACTIVE:
        return min(eligible, key=lambda pair: pair[1].active_conversation_count())[0]
    # ROUND_ROBIN (and any other automatic strategy): a persisted cursor on
    # the queue makes the rotation deterministic and stable across restarts.
    locked_queue = Queue.objects.select_for_update().get(pk=queue.pk)
    idx = locked_queue.round_robin_cursor % len(eligible)
    locked_queue.round_robin_cursor = (locked_queue.round_robin_cursor + 1) % len(eligible)
    locked_queue.save(update_fields=['round_robin_cursor'])
    return eligible[idx][0]


def auto_assign(conversation, queue, depth=0):
    """Attempt automatic assignment for a MANUAL-exempt queue strategy.
    Returns the (possibly unchanged) conversation; never raises — a lack of
    eligible agents just leaves the conversation queued for manual claim.
    """
    from conversations.models import Conversation, Assignment  # local import: avoid app-loading cycles

    if depth > 5 or queue.assignment_strategy == Queue.Strategy.MANUAL:
        return conversation
    with transaction.atomic():
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if conv.assigned_to_id:
            return conv  # someone else already won the assignment race
        eligible = _eligible_agents(queue)
        if not eligible:
            if queue.fallback_queue_id and queue.fallback_queue_id != queue.id:
                return auto_assign(conv, queue.fallback_queue, depth=depth + 1)
            return conv
        agent = _pick_agent(queue, eligible)
        conv.assigned_to = agent
        conv.team = queue.team
        conv.queue = queue
        conv.status = Conversation.Status.OPEN
        conv.save(update_fields=['assigned_to', 'team', 'queue', 'status'])
        Assignment.objects.create(
            conversation=conv, action=Assignment.Action.AUTO_ASSIGN, assigned_to=agent,
            new_team=queue.team, reason=f'Auto-assigned via {queue.assignment_strategy}',
        )
    publish_event(
        'CONVERSATION_ASSIGNED', conv.workspace_id, conversation_id=conv.id, actor_type='SYSTEM',
        payload={'assigned_to': str(agent.id), 'strategy': queue.assignment_strategy},
    )
    return conv


def route_to_queue(conversation, queue, actor=None):
    """Enter (or re-enter) a conversation into a queue: seed the queue's
    default priority (only when the conversation is still at the model's
    NORMAL baseline — an operator's already-explicit priority is never
    downgraded by routing), then attempt automatic assignment when the
    queue's strategy calls for it.
    """
    from conversations.models import Conversation, PriorityChange

    with transaction.atomic():
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        conv.queue = queue
        conv.team = queue.team
        update_fields = ['queue', 'team']
        if conv.priority == Conversation.Priority.NORMAL and queue.default_priority != Conversation.Priority.NORMAL:
            conv.priority = queue.default_priority
            update_fields.append('priority')
            PriorityChange.objects.create(
                conversation=conv, previous_priority=Conversation.Priority.NORMAL, priority=queue.default_priority,
                reason_code=PriorityChange.Reason.QUEUE_DEFAULT, reason=f'Entered queue "{queue.name}"',
            )
        conv.save(update_fields=update_fields)
    publish_event(
        'CONVERSATION_QUEUED', conv.workspace_id, conversation_id=conv.id,
        actor_id=actor.id if actor else None, actor_type='USER' if actor else 'SYSTEM',
        payload={'queue_id': str(queue.id)},
    )
    if queue.assignment_strategy != Queue.Strategy.MANUAL:
        conv = auto_assign(conv, queue)
    return conv


def route_new_conversation(conversation):
    """Best-effort automatic queue entry for a freshly-created conversation:
    picks the highest-routing-priority active queue in the conversation's
    workspace whose supported_categories is either empty (a catch-all) or
    contains the conversation's category. No matching queue simply leaves
    the conversation unqueued for manual triage — never invents a queue.
    """
    candidates = Queue.objects.filter(workspace=conversation.workspace, is_active=True).order_by('routing_priority', 'name')
    for queue in candidates:
        if not queue.supported_categories or conversation.category in queue.supported_categories:
            return route_to_queue(conversation, queue)
    return conversation


def claim_conversation(conversation, user):
    """A human agent claims an unassigned conversation for themselves.
    Concurrency-safe: only the first of two simultaneous claimants wins."""
    from conversations.models import Conversation, Assignment
    from conversations.services import broadcast_ops_event, conversation_summary
    from audit.models import AuditEvent

    with transaction.atomic():
        conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
        if conv.assigned_to_id:
            raise AssignmentError('Conversation is already assigned')
        if conv.team_id and not TeamMembership.objects.filter(
            team_id=conv.team_id, user=user, is_active=True,
        ).exists():
            raise AssignmentError('You are not a member of this conversation’s team')
        presence = touch_presence(user)
        capacity = (conv.queue.max_active_per_agent if conv.queue_id and conv.queue.max_active_per_agent
                    else presence.max_capacity)
        if presence.is_at_capacity(capacity):
            raise AssignmentError('You are at capacity')
        conv.assigned_to = user
        conv.status = Conversation.Status.OPEN
        conv.save(update_fields=['assigned_to', 'status'])
        Assignment.objects.create(
            conversation=conv, action=Assignment.Action.CLAIM, assigned_to=user, new_team=conv.team,
        )
    AuditEvent.objects.create(
        actor=user, action='conversation_claimed', target_type='conversation', target_id=str(conv.id),
    )
    broadcast_ops_event(conv.id, 'conversation.assigned', conversation_summary(conv))
    publish_event(
        'CONVERSATION_ASSIGNED', conv.workspace_id, conversation_id=conv.id, actor_id=user.id, actor_type='USER',
        payload={'assigned_to': str(user.id), 'claimed': True},
    )
    return conv
