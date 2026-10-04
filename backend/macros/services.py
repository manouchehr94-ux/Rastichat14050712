"""Macro execution engine: preview (zero side effects), execute (idempotent
via a unique idempotency_key, per-action continue-on-error, same
SUCCEEDED/PARTIALLY_SUCCEEDED/FAILED aggregation rule as the automations
engine), and retry (re-runs only actions that never succeeded).
"""
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from audit.models import AuditEvent

from . import actions as macro_actions
from .models import Macro, MacroActionExecution, MacroExecution
from .templating import MacroTemplateError, resolve_macro_template


class MacroError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _audit(actor, action, macro, metadata=None):
    AuditEvent.objects.create(
        actor=actor, action=action, target_type='macro', target_id=str(macro.id), metadata=metadata or {},
    )


def _describe_action(action, conversation):
    """Human-readable, read-only preview line for one action — resolves
    display names (team/tag/article) but performs NO mutation whatsoever.
    """
    action_type = action.get('type')
    params = action.get('params') or {}
    workspace = conversation.workspace

    if action_type == 'SEND_REPLY':
        try:
            preview_text = resolve_macro_template(params.get('template', ''), conversation)
        except MacroTemplateError as exc:
            return {'type': action_type, 'error': str(exc)}
        return {'type': action_type, 'preview': preview_text}
    if action_type == 'SEND_ARTICLE':
        from knowledge_base.models import KnowledgeBaseArticle
        article = KnowledgeBaseArticle.objects.filter(id=params.get('article_id'), workspace=workspace).first()
        return {'type': action_type, 'article_title': article.title if article else None}
    if action_type in ('ADD_TAG', 'REMOVE_TAG'):
        from customer_context.models import Tag
        tag = Tag.objects.filter(id=params.get('tag_id'), workspace=workspace).first()
        return {'type': action_type, 'tag_name': tag.name if tag else None}
    if action_type in ('ASSIGN_TO_TEAM', 'TRANSFER_TO_TEAM'):
        from teams.models import Team
        team = Team.objects.filter(id=params.get('team_id'), workspace=workspace).first()
        return {'type': action_type, 'team_name': team.name if team else None}
    if action_type == 'ASSIGN_TO_AGENT':
        agent = workspace.memberships.filter(user_id=params.get('agent_id')).select_related('user').first()
        return {'type': action_type, 'agent_name': agent.user.display_name or agent.user.email if agent else None}
    return {'type': action_type, 'params': params}


def preview_macro(macro, conversation):
    """Read-only: no Message, no assignment, no status change, no tag row,
    no execution/side-effect row of any kind — see tests 12/24.
    """
    if conversation.workspace_id != macro.workspace_id:
        raise MacroError("Conversation does not belong to this macro's workspace", 403)
    return {
        'macro_id': str(macro.id), 'macro_name': macro.name,
        'actions': [_describe_action(a, conversation) for a in macro.actions],
    }


def _run_pending_actions(execution):
    """Runs every action in actions_snapshot whose index doesn't already
    have a SUCCEEDED row — a fresh execution has none, so everything runs;
    a retry of a FAILED/PARTIALLY_SUCCEEDED execution only re-attempts the
    indexes that never succeeded, in place (update_or_create on the same
    action_index), never duplicating an already-succeeded action.

    The final SUCCEEDED/PARTIALLY_SUCCEEDED/FAILED aggregation is computed
    from a FRESH read of every action_execution AFTER the loop — never from
    booleans seeded off the pre-retry (possibly stale FAILED) rows, which
    would otherwise permanently pin the execution at PARTIALLY_SUCCEEDED
    even after a retry fixes every remaining action.
    """
    conv = execution.conversation
    already_succeeded = set(
        execution.action_executions.filter(status=MacroActionExecution.Status.SUCCEEDED).values_list('action_index', flat=True)
    )

    for i, action in enumerate(execution.actions_snapshot):
        if i in already_succeeded:
            continue
        action_type = action.get('type', '')
        params = action.get('params') or {}
        ctx = macro_actions.MacroActionContext(execution_id=str(execution.id), action_index=i, actor=execution.executed_by)
        try:
            result, obj_type, obj_id = macro_actions.execute_action(conv, action, ctx)
            MacroActionExecution.objects.update_or_create(
                execution=execution, action_index=i,
                defaults={
                    'action_type': action_type, 'params_snapshot': params,
                    'status': MacroActionExecution.Status.SUCCEEDED, 'result_summary': result,
                    'affected_object_type': obj_type or '', 'affected_object_id': obj_id or '', 'error_summary': '',
                },
            )
        except macro_actions.ActionError as exc:
            MacroActionExecution.objects.update_or_create(
                execution=execution, action_index=i,
                defaults={
                    'action_type': action_type, 'params_snapshot': params,
                    'status': MacroActionExecution.Status.FAILED, 'error_summary': str(exc)[:500],
                },
            )
        except Exception:
            MacroActionExecution.objects.update_or_create(
                execution=execution, action_index=i,
                defaults={
                    'action_type': action_type, 'params_snapshot': params,
                    'status': MacroActionExecution.Status.FAILED, 'error_summary': 'Internal error while running this action.',
                },
            )

    final_statuses = set(execution.action_executions.values_list('status', flat=True))
    any_succeeded = MacroActionExecution.Status.SUCCEEDED in final_statuses
    any_failed = MacroActionExecution.Status.FAILED in final_statuses
    if any_failed and any_succeeded:
        final_status = MacroExecution.Status.PARTIALLY_SUCCEEDED
    elif any_failed:
        final_status = MacroExecution.Status.FAILED
    else:
        final_status = MacroExecution.Status.SUCCEEDED

    execution.status = final_status
    execution.completed_at = timezone.now()
    execution.save(update_fields=['status', 'completed_at'])
    Macro.objects.filter(pk=execution.macro_id).update(
        execution_count=F('execution_count') + 1, last_executed_at=timezone.now(),
    )
    _audit(execution.executed_by, 'macro_executed', execution.macro, {'execution_id': str(execution.id), 'status': final_status})
    return execution


def execute_macro(macro, conversation, actor, idempotency_key):
    """Idempotent: a second call with the SAME idempotency_key (a double-
    click, or a retried HTTP request after a timeout) returns the ALREADY-
    COMPLETED execution without running anything again. A key that landed
    mid-run (crashed process) is resumed via _run_pending_actions, which
    itself never re-runs an already-SUCCEEDED action.
    """
    if not idempotency_key:
        raise MacroError('idempotency_key is required', 400)
    if not macro.is_active:
        raise MacroError('Macro is not active', 400)
    if conversation.workspace_id != macro.workspace_id:
        raise MacroError("Conversation does not belong to this macro's workspace", 403)

    execution = None
    try:
        with transaction.atomic():
            execution = MacroExecution.objects.create(
                macro=macro, workspace=macro.workspace, conversation=conversation, executed_by=actor,
                idempotency_key=idempotency_key, actions_snapshot=macro.actions,
                status=MacroExecution.Status.PENDING,
            )
    except IntegrityError:
        execution = MacroExecution.objects.get(workspace=macro.workspace, idempotency_key=idempotency_key)
        if execution.status != MacroExecution.Status.PENDING:
            return execution  # already completed — idempotent no-op re-read

    return _run_pending_actions(execution)


def retry_macro_execution(execution, actor):
    if execution.status not in (MacroExecution.Status.FAILED, MacroExecution.Status.PARTIALLY_SUCCEEDED):
        raise MacroError('Only a failed or partially-succeeded execution can be retried', 400)
    return _run_pending_actions(execution)


def cancel_macro_execution(execution, actor):
    if execution.status != MacroExecution.Status.PENDING:
        raise MacroError('Only a pending execution can be cancelled', 400)
    execution.status = MacroExecution.Status.CANCELLED
    execution.completed_at = timezone.now()
    execution.save(update_fields=['status', 'completed_at'])
    return execution
