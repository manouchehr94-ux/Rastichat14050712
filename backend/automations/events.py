"""The canonical trigger-event envelope and the single small publishing
interface every domain service calls after a successful mutation —
avoiding "scattered direct engine calls across every view" per the spec.

Loop protection design: a `contextvars.ContextVar` holds the
(correlation_id, depth) of the automation action currently executing, if
any. When a domain service calls publish_event() *from within* an
automation action (e.g. SET_PRIORITY action calls
conversations.services.set_priority(), which itself calls publish_event()
for CONVERSATION_PRIORITY_CHANGED), the new event inherits the same
correlation_id and depth+1 — instead of minting a fresh root chain. This is
what lets automations/engine.py bound an entire causal chain (Rule A fires
-> its action changes priority -> that publishes a new event -> Rule B
might match it -> ...) by depth and by total actions-per-correlation,
without any domain service needing to know automation exists.
"""
import contextvars
import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

MAX_AUTOMATION_DEPTH = 5

_current_context = contextvars.ContextVar('automation_action_context', default=None)


@dataclass
class TriggerEvent:
    event_type: str
    workspace_id: str
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation_id: str | None = None
    actor_id: str | None = None
    actor_type: str = 'SYSTEM'
    occurred_at: object = field(default_factory=timezone.now)
    payload: dict = field(default_factory=dict)
    source: str = 'system'
    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    depth: int = 0


class AutomationActionContext:
    """Entered by the engine around each action's execution so any event
    that action's underlying service call publishes inherits this chain's
    correlation_id/depth instead of starting a new root.
    """
    def __init__(self, correlation_id, depth):
        self.correlation_id = correlation_id
        self.depth = depth
        self._token = None

    def __enter__(self):
        self._token = _current_context.set((self.correlation_id, self.depth))
        return self

    def __exit__(self, *exc_info):
        _current_context.reset(self._token)


def current_correlation():
    """(correlation_id, depth) of the in-progress automation action, if
    publish_event() is being called as a side effect of one — else None.
    """
    return _current_context.get()


def publish_event(event_type, workspace_id, *, conversation_id=None, actor_id=None, actor_type='SYSTEM', payload=None, source='system'):
    """Build the canonical envelope and schedule automation processing for
    it once the current transaction commits (or immediately if there is
    none). Never raises on the caller's behalf — a broken automation rule
    must never break the domain action that triggered it.
    """
    ctx = current_correlation()
    if ctx:
        correlation_id, parent_depth = ctx
        depth = parent_depth + 1
    else:
        correlation_id = uuid.uuid4()
        depth = 0

    event = TriggerEvent(
        event_type=event_type,
        workspace_id=str(workspace_id),
        conversation_id=str(conversation_id) if conversation_id else None,
        actor_id=str(actor_id) if actor_id else None,
        actor_type=actor_type,
        payload=payload or {},
        source=source,
        correlation_id=correlation_id,
        depth=depth,
    )

    def _run():
        from .engine import process_event
        try:
            process_event(event)
        except Exception:
            import logging
            logging.getLogger('automations').exception('Automation event processing failed for %s', event.event_id)

    transaction.on_commit(_run)
    return event
