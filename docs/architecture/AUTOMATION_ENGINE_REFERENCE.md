# Automation Engine — Trigger/Action Registry and Rule Schema Reference

A concise reference for the declarative rule format used by `automations.AutomationRule`, not a theoretical architecture document. The authoritative source is always `backend/automations/schema.py` — this doc mirrors it for humans building or reviewing rules.

## Rule shape

```json
{
  "name": "Escalate urgent VIP messages",
  "trigger_type": "CUSTOMER_MESSAGE_CREATED",
  "conditions": { "field": "customer.score", "operator": "greater_than", "value": 80 },
  "actions": [{ "type": "ESCALATE", "params": { "reason": "VIP customer message" } }],
  "priority": 100,
  "stop_processing": false
}
```

`conditions`/`actions` are strictly schema-validated JSON — never executable code (no `eval`, no dynamic ORM field traversal, no user-defined regular expressions). Every field name, operator, and action type is checked against the hardcoded registries below at save time (`AutomationRuleSerializer.validate` → `schema.validate_rule_payload`) and re-checked defensively at evaluation time.

## Triggers

| Code | Fired from |
|---|---|
| `CONVERSATION_CREATED` | `StartCustomerChatView` (widget start) |
| `CONVERSATION_QUEUED` | `queues.services.route_to_queue` |
| `CONVERSATION_ASSIGNED` | `queues.services.auto_assign`, `.claim_conversation`, `conversations.services._reassign` |
| `CONVERSATION_UNASSIGNED` | `conversations.services.unassign` |
| `CONVERSATION_TRANSFERRED` | `conversations.services.transfer_team` |
| `CONVERSATION_ESCALATED` | `conversations.services.escalate` |
| `CONVERSATION_PRIORITY_CHANGED` | `conversations.services.set_priority` |
| `CONVERSATION_STATUS_CHANGED` | *(reserved — no direct publisher yet; status changes currently surface via the more specific CLOSED/REOPENED/RESOLVED triggers)* |
| `CUSTOMER_MESSAGE_CREATED` | `WidgetChatConsumer._save_visitor_message` |
| `OPERATOR_MESSAGE_CREATED` | `SendMessageView`, `DashboardChatConsumer._save_user_message` |
| `INTERNAL_NOTE_CREATED` | `CustomerConversationViewSet.internal_notes` (POST) |
| `SLA_APPROACHING` / `SLA_BREACHED` | `evaluate_sla` management command |
| `RATING_SUBMITTED` | `WidgetRateConversationView` |
| `CONVERSATION_RESOLVED` | `conversations.services.close_conversation` (via `sla.services.mark_resolved`) |
| `CONVERSATION_CLOSED` | `conversations.services.close_conversation` |
| `CONVERSATION_REOPENED` | `conversations.services.reopen_conversation` |
| `SCHEDULED_TIME_REACHED` | `process_automation_jobs` (synthetic — one per executed `ScheduledAction`) |

Every event is published via the single `automations.events.publish_event()` interface, after the triggering write has committed (`transaction.on_commit`), and is idempotently recorded by `event_id` before any rule runs.

## Condition grammar

```
{"all": [<condition>, ...]}          — every child must match
{"any": [<condition>, ...]}          — at least one child must match
{"not": <condition>}                 — negates a single child
{"field": "<name>", "operator": "<op>", "value": <any>}   — leaf
{}                                    — matches everything (no conditions configured)
```

Nesting depth is capped at 6, and a single rule's condition tree is capped at 40 leaf/group nodes total (`MAX_CONDITION_DEPTH`, `MAX_CONDITIONS_TOTAL`).

### Condition fields

| Field | Type | Notes |
|---|---|---|
| `conversation.type`, `.status`, `.priority`, `.sla_state` | string | `sla_state` ∈ `none`/`on_track`/`approaching`/`breached` |
| `conversation.queue_id`, `.team_id`, `.assignee_id` | string | compared as UUID strings |
| `conversation.assigned` | boolean | |
| `conversation.created_at` | datetime | ISO 8601 |
| `conversation.waiting_minutes`, `.message_count`, `.customer_message_count`, `.operator_message_count`, `.rating` | number | |
| `customer.tags` | list | conversation tags (names) |
| `customer.score`, `.order_count`, `.total_spending` | number | |
| `customer.location` | string | Persian-normalized for `contains`/`starts_with`/`ends_with` |
| `customer.metadata` | metadata | requires a `"path"` key — a safe dict-key lookup into `CustomerProfile.metadata`, never arbitrary attribute traversal |
| `message.type`, `.content`, `.sender_type`, `.attachment_type` | string | only populated for message-triggered events; `content` is Persian-normalized |
| `operational.business_hours` | boolean | reuses `sla.services._working_windows_for_date` — never duplicated |
| `operational.agent_online`, `.agent_at_capacity`, `.queue_has_capacity`, `.assignee_is_team_member` | boolean | |

### Operators

| Type | Allowed operators |
|---|---|
| string | `equals`, `not_equals`, `in`, `not_in`, `contains`, `not_contains`, `starts_with`, `ends_with`, `exists`, `not_exists` |
| number | `equals`, `not_equals`, `greater_than`, `greater_than_or_equal`, `less_than`, `less_than_or_equal`, `in`, `not_in`, `exists`, `not_exists` |
| datetime | `before`, `after`, `exists`, `not_exists` |
| boolean | `is_true`, `is_false`, `exists`, `not_exists` |
| list | `contains`, `not_contains`, `in`, `not_in`, `exists`, `not_exists` |
| metadata | string ∪ number ∪ boolean operators |

`exists`/`not_exists`/`is_true`/`is_false` take no `"value"`; every other operator requires one.

**Persian keyword matching** (`message.content`, `customer.location`): ی/ي and ک/ك are normalized, whitespace is collapsed, and comparison is casefolded — never a user-supplied regular expression.

## Actions

Every handler reuses an existing approved domain service (`conversations.services`, `queues.services`, `notifications.services`) — the two exceptions (`ADD_TAG`/`REMOVE_TAG`) are a plain `get_or_create`/`delete` against a pure join table with no business logic to duplicate.

| Type | Required params | Optional params |
|---|---|---|
| `ASSIGN_TO_AGENT` | `agent_id` (workspace member) | |
| `ASSIGN_TO_TEAM` | `team_id` | |
| `ASSIGN_USING_QUEUE_STRATEGY` | `queue_id` | |
| `UNASSIGN` | — | |
| `RETURN_TO_QUEUE` | — | |
| `TRANSFER_TO_TEAM` | `team_id` | `reason` |
| `ESCALATE` | — | `reason` |
| `SET_PRIORITY` | `priority` ∈ `LOW`/`NORMAL`/`HIGH`/`URGENT` | |
| `SET_STATUS` | `status` ∈ `OPEN`/`PENDING`/`CLOSED`/`WAITING_FOR_WORKSPACE`/`WAITING_FOR_PLATFORM`/`RESOLVED` | |
| `ADD_TAG` / `REMOVE_TAG` | `tag_id` | |
| `SEND_CUSTOMER_MESSAGE` | `template` (≤2000 chars) | |
| `CREATE_INTERNAL_NOTE` | `content` (≤4000 chars) | |
| `SEND_NOTIFICATION` | `title` (≤255 chars) | `target` ∈ `ASSIGNEE`/`TEAM_SUPERVISORS`/`WORKSPACE_ADMINS` (default `ASSIGNEE`) |
| `NOTIFY_SUPERVISOR` | `title` (≤255 chars) | |
| `REQUEST_RATING` | — | |
| `CLOSE_CONVERSATION` / `REOPEN_CONVERSATION` | — | |
| `SCHEDULE_ACTION` | `delay_minutes` (0–20160), `action` (a nested action, any type except `SCHEDULE_ACTION`/`CANCEL_SCHEDULED_ACTION`) | |
| `CANCEL_SCHEDULED_ACTION` | — | `idempotency_key` |

`agent_id`/`team_id`/`queue_id`/`tag_id` are always validated to belong to the rule's own workspace, both at save time and again by the action handler at execution time.

### Automated customer messages

`SEND_CUSTOMER_MESSAGE`/`CREATE_INTERNAL_NOTE`/`REQUEST_RATING` are sent as `Message.SenderType.SYSTEM` — the widget and dashboard render these exactly like any other message, clearly attributable to automation rather than a human agent. `SEND_CUSTOMER_MESSAGE` templates support safe `{variable}` interpolation (whitelist-regex substitution, never `.format()`/`eval`):

`customer_name`, `store_name`, `conversation_id`, `queue_name`, `team_name`, `agent_name`, `business_hours_summary`

An unrecognized `{token}` is left as literal text — never evaluated, never an error, never a source of internal-data leakage. Every automated message/note carries a deterministic `client_message_id` derived from `(execution_id, action_index)`, so re-processing the same execution can never duplicate it.

## Loop protection

- `correlation_id`/`depth` propagate through a `contextvars`-based context: an action's own domain-service call that publishes a further event inherits the same `correlation_id` with `depth + 1`, rather than starting a new chain.
- `MAX_AUTOMATION_DEPTH = 5` — an event at a deeper depth is dropped without processing.
- `MAX_ACTIONS_PER_CORRELATION = 40` — once a chain has recorded this many action executions, further processing for that `correlation_id` stops.
- A rule that already produced `MATCHED`/`SUCCEEDED`/`PARTIALLY_SUCCEEDED` for the current `correlation_id` is never evaluated again in that chain (`SKIPPED_LOOP`).

These three bounds are structural — they hold regardless of what rules an admin configures, so a two-rule ping-pong (e.g. Rule A sets priority HIGH→URGENT, Rule B sets it back) always terminates deterministically rather than looping or crashing the worker.

## Deterministic execution policy

- Rules for one event run in `priority` ascending, then `created_at` ascending order.
- Every rule evaluated for a given event sees the **same** conversation snapshot, fetched once at the start of processing — a rule's own actions are not re-observed by a later rule's conditions within the same pass (actions still take real effect in the database immediately; only the condition-evaluation snapshot is fixed per event).
- `stop_processing: true` on a rule that reaches `SUCCEEDED`/`PARTIALLY_SUCCEEDED` prevents any lower-priority rule from running for that event.

## Simulation

`automations/simulation.py` never imports `execute_action` or `process_event` — there is no code path by which a simulation request can reach a real mutation, message, notification, assignment, or scheduled job. It reuses `evaluate_condition`/`resolve_field` directly and returns a trace of which condition nodes matched plus a preview of the actions that would run.
