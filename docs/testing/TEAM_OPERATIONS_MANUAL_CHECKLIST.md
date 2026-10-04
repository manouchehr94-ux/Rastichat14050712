# Team Operations, Queues and SLA — Manual QA Checklist

For a human reviewer verifying this pass in a running environment (see `docs/runbooks/TEAM_OPERATIONS_LOCAL_RUN.md`). Each item cites the automated coverage that already exists, so this checklist is for spot-checking what's hard to assert automatically, not re-deriving what the test suites already prove.

## Teams and queues

- [ ] As Owner/Admin, create a team, add/remove members; confirm a regular operator can see teams but cannot create/edit them (403). *(automated: `teams/tests.py`)*
- [ ] Create queues with each assignment strategy (MANUAL/ROUND_ROBIN/LEAST_ACTIVE/RANDOM); confirm a MANUAL queue never auto-assigns and the others do. *(automated: `queues/tests.py::RoutingStrategyTests`)*
- [ ] Confirm a queue's `fallback_queue` is used when its own team has no eligible agents. *(automated)*

## Assignment and claim

- [ ] New customer conversation lands in the correct queue, unassigned, with the "واگذارنشده" badge. *(automated: E2E)*
- [ ] Click "برداشتن" (claim) on an unassigned conversation; button disappears, conversation is now yours. *(automated: E2E)*
- [ ] Open the same unassigned conversation in two operator sessions and claim simultaneously — exactly one succeeds, the other sees a clear "already assigned" error, never a silent double-assignment. *(automated: `queues/tests.py::ConcurrentClaimTests` with real threads, plus E2E with two real browser contexts)*
- [ ] Transfer a conversation to a different team; confirm it's unassigned and queue-cleared so the receiving team claims explicitly, and the assignment-history panel shows the transfer with old/new team. *(automated: `conversations/tests_team_ops.py`, E2E)*
- [ ] Escalate a conversation; confirm it routes to a team SUPERVISOR (or the team manager as fallback) and priority jumps to URGENT if it wasn't already. *(automated)*
- [ ] Assignment-history panel never loses or overwrites a prior entry — reassigning twice shows both events, oldest first. *(automated)*

## Agent presence and capacity

- [ ] Set your own status to BUSY / DO_NOT_DISTURB via the presence control; confirm it does not silently revert on the next activity ping (only ONLINE/AWAY decay from inactivity). *(automated: `accounts` presence tests)*
- [ ] Set an agent's capacity to 0 (or fill their active conversations to `max_capacity`); confirm a new conversation on a LEAST_ACTIVE/ROUND_ROBIN queue skips them and goes to another eligible agent instead. *(automated: `queues/tests.py`, E2E)*
- [ ] Confirm an OFFLINE or DO_NOT_DISTURB agent is never auto-assigned a new conversation. *(automated)*

## Priority and SLA

- [ ] Change a conversation's priority from the header dropdown; confirm the badge updates and a `PriorityChange` audit record is created. *(automated)*
- [ ] Force a conversation's `first_response_due_at` into the near future, run `python manage.py evaluate_sla`, confirm the "⏳ X تا موعد" approaching badge appears — then run the evaluator again and confirm no duplicate notification fires. *(automated: `sla/tests.py::SLAEvaluatorIdempotencyTests`, E2E)*
- [ ] Force a due date into the past, run the evaluator, confirm the "⏰ نقض SLA" badge appears and (for a resolution breach) the conversation auto-escalates. *(automated)*
- [ ] Create a `BusinessCalendar` with working hours Mon–Fri 9–17 plus a holiday; confirm an SLA policy using that calendar skips weekends/holidays/after-hours when computing the deadline (spot-check one deadline by hand against the calendar). *(automated: `sla/tests.py::BusinessHoursCalculationTests`, 8 cases including DST)*
- [ ] Close a conversation, reopen it; confirm only resolution-related SLA fields reset — first-response timestamps are untouched. *(automated: `conversations/tests_team_ops.py::ReopenSLARecalculationTests`)*

## Collaboration

- [ ] Post an internal note; confirm it renders inline with a distinct "🔒 یادداشت داخلی" badge in the operator timeline, and never appears in the customer widget even after waiting a few seconds for any stray realtime delivery. *(automated: `collaboration/tests.py::InternalNoteVisibilityTests`, E2E)*
- [ ] Mention a colleague in a note; confirm they get an in-app notification, and that mentioning a user outside the workspace is silently rejected (not an error, just excluded). *(automated)*
- [ ] Create quick replies at PERSONAL/TEAM/WORKSPACE scope; confirm each is visible only to its intended audience (e.g. a personal quick reply from operator A never appears for operator B). *(automated: `collaboration/tests.py::QuickReplyVisibilityTests`)*
- [ ] Use a quick reply containing `{customer_name}` and an unrecognized `{something_else}` token; confirm the known token resolves and the unknown one is left as literal text, not evaluated or blanked. *(automated: `QuickReplyVariableResolutionTests`)*

## Supervisor dashboard and notifications

- [ ] As Owner/Admin/team-SUPERVISOR, open `/supervisor`; confirm unassigned/waiting/urgent/approaching/breached counts and by-queue/by-team/by-agent breakdowns render, and agents at capacity are visually flagged. *(automated: `conversations/tests_team_ops.py::SupervisorSummaryTests`, `supervisor/page.test.tsx`)*
- [ ] As a regular operator, confirm `/supervisor` shows a clear "دسترسی محدود شده است" message, not a raw error or blank page. *(automated)*
- [ ] Notification bell shows an unread count, opens a list, marking one read (or "mark all read") updates the badge; confirm you never see another user's notifications. *(automated: `notifications/tests.py`)*

## Cross-tenant / security spot checks

- [ ] Log in to workspace A; confirm teams, queues, and notifications belonging to workspace B never appear in any list, filter dropdown, or count. *(automated: E2E tenant-isolation scenario, which seeds a second platform/workspace/team/queue/notification and asserts none of them ever render)*
- [ ] Attempt to add a team member from a different workspace; confirm 404, not a silent no-op or 403 leaking the target's existence. *(automated: `teams/tests.py`)*
- [ ] Confirm the existing customer chat (text/image/voice/product-share/rating) and the platform support ticket flow still work end-to-end after all of the above. *(automated: full `customer-operator-flow.spec.ts` suite re-run alongside this stage's new spec)*
