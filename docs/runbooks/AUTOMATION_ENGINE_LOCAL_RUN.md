# Automation and Workflow Engine — Local Run

Builds on `docs/runbooks/TEAM_OPERATIONS_LOCAL_RUN.md` (which itself builds on `RICH_CHAT_LOCAL_RUN.md`) — start Postgres/Redis, the backend, the widget, and the operator dashboard first. This doc only covers what's new in this stage.

## Migrations

| App | Migration | What it adds |
|---|---|---|
| `automations` | `0001_initial` | `AutomationRule`, `AutomationEvent`, `AutomationExecution`, `AutomationActionExecution`, `ScheduledAction` |
| `customer_context` | `0002_customerprofile_metadata` | `CustomerProfile.metadata` (JSONField, read by the `customer.metadata` condition) |
| `notifications` | `0002_alter_notification_event_type` | `Notification.EventType.AUTOMATION_TRIGGERED` |

All applied by the same `python manage.py migrate` from the base runbook.

## Seed data

No changes to `seed_data.py` for this stage — the existing `admin@ws.com` (WORKSPACE_ADMIN) account is who can access `/automations`; `operator@ws.com`/`operator2@ws.com` (WORKSPACE_OPERATOR) are correctly denied.

## New REST endpoints

All under `/api/v1/automations/`, all workspace-admin-only (Owner/Admin), scoped by role-specific membership so being admin in one workspace never surfaces another workspace's rules.

- `rules/`, `rules/<id>/` — CRUD
- `rules/<id>/activate/`, `/deactivate/`, `/duplicate/`, `/simulate/` — POST
- `registry/` — GET: available triggers, condition fields (with allowed operators), action types (with parameter shapes) — drives the rule-builder UI
- `validate/` — POST: structural validation of a draft `{conditions, actions}` without saving
- `simulate/` — POST: dry-run a draft (or saved) rule against an optional `conversation_id` — zero side effects
- `execution-history/` — GET, filterable by `rule`, `conversation`, `status`, `correlation_id`
- `scheduled-actions/`, `scheduled-actions/<id>/cancel/` — GET / POST

## Automation UI

`http://localhost:3000/automations` (linked from the inbox header's ⚙️ icon) — rule list, visual builder (nested ALL/ANY/NOT condition editor, registry-driven action editor), starter templates (never auto-activate), in-builder simulation, execution history, scheduled actions.

## `process_automation_jobs`

Processes due `ScheduledAction` rows created by the `SCHEDULE_ACTION` action:

```bash
cd backend
python manage.py process_automation_jobs
```

Idempotent and concurrency-safe: each due job is claimed with `SELECT ... FOR UPDATE SKIP LOCKED` and flipped to `RUNNING` inside a short transaction before the actual action runs outside that lock, so two overlapping workers can never execute the same job twice. A failed job returns to `PENDING` for a future retry until `max_attempts` (default 3) is reached, at which point it's marked permanently `FAILED`. Not wired to a scheduler in this stage — run it manually, via cron, or (later) a Celery beat task; nothing here assumes Celery exists.

```bash
python manage.py process_automation_jobs --batch-size 200   # default 200
```

## Manual verification of the loop-protection guarantee

```bash
python manage.py shell -c "
from workspaces.models import Workspace
from automations.models import AutomationRule
ws = Workspace.objects.get(name='Sample Workspace')
AutomationRule.objects.create(
    workspace=ws, name='A', trigger_type='CONVERSATION_PRIORITY_CHANGED', is_active=True,
    conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'HIGH'},
    actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'URGENT'}}],
)
AutomationRule.objects.create(
    workspace=ws, name='B', trigger_type='CONVERSATION_PRIORITY_CHANGED', is_active=True,
    conditions={'field': 'conversation.priority', 'operator': 'equals', 'value': 'URGENT'},
    actions=[{'type': 'SET_PRIORITY', 'params': {'priority': 'HIGH'}}],
)
"
```

Then change any conversation's priority to HIGH from the dashboard — the chain settles after each rule fires once (visible as `SKIPPED_LOOP` rows in the execution history for every subsequent attempt), never loops, never crashes the process.

## Known limitations

- `CONVERSATION_STATUS_CHANGED` is a registered trigger type but has no dedicated publisher yet — status transitions are currently observable via the more specific `CONVERSATION_CLOSED`/`CONVERSATION_REOPENED`/`CONVERSATION_RESOLVED` triggers instead. A rule that specifically wants "any status change" cannot be expressed yet.
- `process_automation_jobs` is a management command, not a running daemon — nothing in this stage schedules it. A cron entry or Celery beat task must be added separately before delayed actions execute promptly in a real deployment (a `SCHEDULE_ACTION` due five minutes ago will still execute correctly whenever the command next runs; it is queued, not lost).
- `SEND_NOTIFICATION`/`NOTIFY_SUPERVISOR` write a `Notification` row but do not push to any external channel (email/SMS/push) — matches the existing in-app-only notification system from the team-operations stage.
- The rule builder's `agent_id` (`ASSIGN_TO_AGENT`) parameter is a plain text field for the operator's UUID rather than a searchable picker — the same is true of the `SCHEDULE_ACTION` nested-action editor, which only goes one level deep (matches the backend's own ban on nested `SCHEDULE_ACTION`).
- `AutomationRule.conditions`/`.actions` have no UI-level "preview as JSON" toggle — by design, per the requirement to never expose raw JSON as the primary UX; an admin who needs to inspect the literal JSON must currently use the API or Django admin directly.
- Simulation's condition trace shows the actual resolved value for a leaf condition but does not currently explain *why* a nested `all`/`any` group failed beyond marking it ✗ — an admin must expand into the children to see which leaf(s) didn't match.
