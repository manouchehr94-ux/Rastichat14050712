# Team Operations, Queues and SLA — Local Run

Builds on `docs/runbooks/RICH_CHAT_LOCAL_RUN.md` steps 1–5 (Postgres/Redis, backend, widget, operator dashboard, platform dashboard) — start those first. This doc only covers what's new in this stage: migrations, endpoints, WebSocket events, and the SLA evaluator.

## Migrations

Five new apps, plus two extensions to existing apps. All applied by the same `python manage.py migrate` from the base runbook.

| App | Migration | What it adds |
|---|---|---|
| `teams` | `0001_initial` | `Team`, `TeamMembership` |
| `queues` | `0001_initial`, `0002_initial` | `Queue` (routing strategy, fallback, round-robin cursor) |
| `sla` | `0001_initial`, `0002_initial`, `0003_holiday_end_time_holiday_start_time` | `BusinessCalendar`, `WorkingInterval`, `Holiday`, `SLAPolicy`, `ConversationSLA` |
| `collaboration` | `0001_initial`, `0002_initial` | `Mention`, `QuickReply` |
| `notifications` | `0001_initial` | `Notification` |
| `accounts` | `0004_operatorpresence_max_capacity_and_more` | `OperatorPresence.max_capacity`, `BUSY`/`DO_NOT_DISTURB` statuses |
| `conversations` | `0004_prioritychange_assignment_action`, `0005_assignment_new_team_assignment_previous_assignee_and_more` | `Conversation.priority`/`queue`/`team`/`resolved_at`, `PriorityChange`, extended `Assignment` (action/previous_assignee/previous_team/new_team/reason), `Message.MessageType.INTERNAL_NOTE` |

## Seed data

`python seed_data.py` (re-run is idempotent) now also creates: a second operator (`operator2@ws.com` / `pass1234`), a "فروش" team (both operators, operator2 as SUPERVISOR), a MANUAL "صف فروش" queue, a "فنی" team (transfer destination), and a fast-fuse SLA policy ("Fast E2E SLA": 1 min first-response / 2 min resolution) used to exercise SLA states without waiting on real wall-clock time.

## New REST endpoints

All under `/api/v1/`, all tenant-scoped to the caller's workspace membership.

- `teams/`, `teams/<id>/`, `teams/<id>/add_member/`, `teams/<id>/remove_member/` — Owner/Admin manage; operators can list/retrieve only.
- `queues/`, `queues/<id>/` — same permission split as teams.
- `business-calendars/`, `business-calendars/<id>/`, `business-calendars/<id>/add_interval/`, `business-calendars/<id>/add_holiday/`
- `sla-policies/`, `sla-policies/<id>/`
- `quick-replies/`, `quick-replies/<id>/`, `quick-replies/<id>/use/` (increments usage, resolves `{customer_name}`/`{agent_name}`/`{store_name}`/`{conversation_id}` if `conversation_id` is given)
- `notifications/`, `notifications/unread_count/`, `notifications/<id>/mark_read/`, `notifications/mark_all_read/` — strictly scoped to `request.user`
- `conversations/customer/<id>/claim/`, `/transfer/`, `/unassign/`, `/return_to_queue/`, `/escalate/`, `/set_priority/`, `/assignment_history/` (GET), `/internal_notes/` (GET list / POST create, mentions via `mentioned_user_ids`)
- `conversations/customer/<id>/sla/` — read-only `ConversationSLA` detail
- `supervisor/summary/` — Owner/Admin/team-SUPERVISOR only (`IsSupervisorOrAdmin`); accepts `?workspace_id=`/`?hours=24`

`conversations/customer/` list also gained query filters: `queue`, `team`, `priority`, `assignee`, `unassigned=1`, `mine=1`, `sla_status=breached|approaching`.

## WebSocket events

Existing widget-visible group (`chat_<conv_id>`) is unchanged. A new operator-only group (`chat_ops_<conv_id>`) — joined only by the dashboard socket, never the widget socket — carries 10 lifecycle events; a separate `ws/notifications/<token>/` socket carries 2 more.

`chat_ops_<conv_id>` (dashboard-only): `conversation.queued`, `conversation.assigned`, `conversation.unassigned`, `conversation.transferred`, `conversation.escalated`, `conversation.priority_updated`, `conversation.sla_updated`, `conversation.sla_approaching`, `conversation.sla_breached`, `conversation.internal_note_created`.

`ws/notifications/<token>/`: `notification.created` (per-user group `notifications_<user_id>`), `agent.presence_updated` (per-workspace group `workspace_presence_<workspace_id>`, sent only on an explicit status change, not every activity ping).

## SLA breach evaluator

```bash
cd backend
python manage.py evaluate_sla
```

Idempotent and safe to run repeatedly or on a schedule (cron/Celery beat) — each of the three SLA clocks (first-response, next-response, resolution) has its own `*_breached_at`/`*_approaching_notified_at` guard field, checked under `select_for_update()` per `ConversationSLA` row, so re-running never double-fires a breach/approaching notification. A resolution breach auto-escalates the conversation; a first/next-response breach on a LOW/NORMAL-priority conversation bumps it to HIGH.

For manual QA, force a deterministic state instead of waiting on real time:

```bash
python manage.py shell -c "
from django.utils import timezone
from datetime import timedelta
from sla.models import ConversationSLA
sla = ConversationSLA.objects.get(conversation_id='<uuid>')
sla.first_response_due_at = timezone.now() - timedelta(minutes=1)  # or + timedelta(seconds=5) for 'approaching'
sla.first_response_breached_at = None
sla.first_response_approaching_notified_at = None
sla.save()
"
python manage.py evaluate_sla
```

## Known limitations

- The SLA evaluator is a management command, not a running daemon — nothing in this stage schedules it. A cron entry or Celery beat task must be added separately before breach detection is live in a real deployment.
- `AuditEvent` (reused, not extended) has no `workspace` field, so it isn't structurally tenant-scoped at the model level. No dedicated "list audit events" endpoint was added in this stage, so this is a latent gap rather than an active one, but should be addressed before any audit-log UI is built on top of it.
- `OperatorPresence.max_capacity` defaults to 10 and is never auto-adjusted; a workspace with heavy conversation volume and few operators will need this raised manually (via the presence API or Django admin) or auto-assignment will start skipping otherwise-available agents.
- Internal notes and mentions have no edit/delete — once posted, they're permanent (matches the "never overwritten" audit-history principle used elsewhere, but is a real product gap if an operator posts a note in error).
- Quick-reply variable resolution only supports the four documented tokens (`customer_name`, `agent_name`, `store_name`, `conversation_id`); any other `{token}` in a quick-reply body is left as literal text rather than resolved or flagged.
