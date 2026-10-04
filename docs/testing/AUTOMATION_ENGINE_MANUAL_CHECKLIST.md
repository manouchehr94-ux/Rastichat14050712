# Automation and Workflow Engine — Manual QA Checklist

For a human reviewer verifying this pass in a running environment (see `docs/runbooks/AUTOMATION_ENGINE_LOCAL_RUN.md`). Each item cites the automated coverage that already exists, so this checklist is for spot-checking what's hard to assert automatically, not re-deriving what the test suites already prove.

## Access control

- [ ] As `operator@ws.com` (WORKSPACE_OPERATOR), visit `/automations`; confirm a clear "دسترسی محدود شده است" message, not a crash or an empty page. *(automated: `automations/tests_api.py`, E2E)*
- [ ] As `admin@ws.com` (WORKSPACE_ADMIN), confirm the page loads and every tab (قوانین / تاریخچه اجرا / اقدامات زمان‌بندی‌شده) works. *(automated: E2E)*
- [ ] As an admin of a *different* workspace, confirm you never see the first workspace's rules, execution history, or scheduled actions, even by ID (try the REST endpoints directly with a known ID from the other workspace — expect 404, not 403 with leaked data). *(automated: `automations/tests_api.py`, E2E)*

## Rule builder

- [ ] Create a rule with a nested `ALL`/`ANY` condition group and a `NOT`; save it; reopen it for editing and confirm the condition tree round-trips exactly (same fields/operators/values, same nesting). *(automated: `automations/tests_schema.py`, frontend component tests)*
- [ ] Add each of the 20 action types once; confirm the parameter fields shown match the action (e.g. `SET_PRIORITY` shows a priority dropdown, `ASSIGN_TO_TEAM` shows your real teams). *(automated: `automations/tests_api.py::test_registry_returns_...`, frontend tests)*
- [ ] Leave a required action parameter empty and try to save; confirm a clear inline error, not a blank page or unhandled exception. *(automated: E2E)*
- [ ] Click a starter template; confirm it only pre-fills the builder — the rule is not created or activated until you explicitly click "ایجاد قانون", and even after creating it, it starts inactive. *(automated: frontend tests, E2E)*
- [ ] Duplicate an active rule; confirm the copy is inactive (never silently doubles up live automation). *(automated)*

## Dry-run simulation

- [ ] Build a rule, enter a real conversation ID, click "اجرای شبیه‌سازی"; confirm it reports whether it matched and which actions *would* run — then confirm the conversation is genuinely untouched (no priority change, no new message, no notification, no scheduled job) even when the simulated actions would have caused all four. *(automated: `automations/tests_simulation.py` — 9 dedicated zero-side-effect tests)*
- [ ] Simulate a rule against a conversation that does *not* match its conditions; confirm it reports no match and shows no action preview. *(automated)*

## Real automation firing

- [ ] Activate a `CONVERSATION_CREATED` → `SET_PRIORITY(URGENT)` rule; start a new customer chat; confirm the conversation shows "فوری" in the operator dashboard without any manual step. *(automated: E2E)*
- [ ] Activate a `CONVERSATION_CLOSED` → `SEND_CUSTOMER_MESSAGE` rule; close a conversation; confirm the customer widget shows the automated message live (no reload needed), clearly a system message, not from a human agent. *(automated: E2E)*
- [ ] Deactivate a rule; trigger its event; confirm it does *not* fire. *(automated: E2E)*
- [ ] Confirm the execution history tab shows a row for every rule evaluation (matched or not), and expanding a row shows per-action detail with a clear ✓/✗ per action. *(automated: E2E)*

## Scheduled actions

- [ ] Activate a rule with `SCHEDULE_ACTION`; confirm a row appears in "اقدامات زمان‌بندی‌شده" with status "در انتظار"; run `python manage.py process_automation_jobs`; confirm it flips to "موفق" and the wrapped action actually happened. *(automated: `automations/tests_scheduling.py`, E2E)*
- [ ] Cancel a pending scheduled action from the UI; run `process_automation_jobs` again; confirm it stays cancelled and never executes. *(automated)*
- [ ] Run `process_automation_jobs` twice in quick succession (or from two terminals at once) against the same due job; confirm it executes exactly once, never twice. *(automated: `ProcessAutomationJobsCommandTests::test_running_command_twice_does_not_double_execute`)*

## Safety guarantees

- [ ] Create the two-rule priority ping-pong from the runbook's "Manual verification" section; trigger it; confirm the conversation's priority settles (does not oscillate forever), the process never crashes, and the execution history shows a bounded number of rows including at least one "رد شده (حلقه)" (SKIPPED_LOOP). *(automated: `automations/tests_engine.py::test_loop_protection_stops_a_two_rule_ping_pong`, E2E)*
- [ ] Create two rules on the same trigger, higher-priority one with "توقف پردازش" (stop_processing) checked; confirm the lower-priority rule never runs once the first succeeds. *(automated, E2E)*
- [ ] Confirm no execution history row, scheduled-action row, or automated message ever contains a raw stack trace, secret, or internal-only field value — only safe, human-readable summaries. *(spot-check by reading a few rows directly)*
