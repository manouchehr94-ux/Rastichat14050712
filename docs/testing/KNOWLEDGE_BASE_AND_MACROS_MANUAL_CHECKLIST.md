# Knowledge Base and Macros — Manual QA Checklist

For a human reviewer verifying this phase in a running environment. Each item cites the automated coverage that already exists, so this checklist is for spot-checking what's hard to assert automatically, not re-deriving what the test suites already prove.

## Knowledge Base — access and visibility

- [ ] As `operator@ws.com`, visit `/knowledge-base/categories`; confirm you can *view* the tree but creating/editing/deleting a category is refused. *(automated: `knowledge_base/tests_api.py::CategoryPermissionTests`, E2E)*
- [ ] As `admin@ws.com`, create a nested category (parent + child); confirm it renders indented under its parent. *(automated, E2E)*
- [ ] Create a DRAFT article, visibility CUSTOMER; confirm `GET /api/v1/kb/public/articles/<slug>/?project_key=...` returns 404 — not visible until published. *(automated: `tests_api.py::test_draft_article_is_private_from_public_api`, E2E)*
- [ ] Create and publish an INTERNAL article; confirm it never appears in the public list/search/detail endpoints, and guessing its (deterministic, slugify-derived) slug still 404s. *(automated: `tests_api.py::test_internal_article_never_reaches_visitor`, E2E)*
- [ ] Publish a CUSTOMER-visibility article; confirm the public list/detail/search endpoints all return it. *(automated, E2E)*
- [ ] As an operator in a *second* workspace, request the first workspace's article by UUID via `/api/v1/kb/articles/<id>/`; confirm 404 (never a 403 that would confirm existence). *(automated: `tests_api.py::test_cross_workspace_uuid_rejected`, E2E)*

## Revisions

- [ ] Edit an article's body twice; confirm the revision history shows 3 entries (initial + 2 edits) with correct `title`/`body` snapshots each. *(automated: `tests_revisions.py`)*
- [ ] Restore an old revision; confirm a *new* revision is appended (never destroys the ones in between) and the article's live content matches the restored one. *(automated: `tests_revisions.py::test_restore_creates_a_new_revision`, E2E)*
- [ ] Change only a category/tag (no title/body/excerpt edit); confirm no new revision is created. *(automated: `tests_revisions.py::test_metadata_only_update_does_not_create_revision`)*

## Search

- [ ] Search using the Arabic Yeh/Kaf forms (`ي`, `ك`) for an article authored with the Persian forms (`ی`, `ک`); confirm it still matches. *(automated: `tests_search.py::test_search_persian_normalization_works`)*
- [ ] Search in English with mixed case (`ReFuNd`); confirm case-insensitive matching. *(automated: `tests_search.py::test_search_english_case_insensitive`)*
- [ ] Run the same search query twice; confirm identical result order both times (deterministic ranking, no flaky relevance scoring). *(automated: `tests_search.py::test_search_ranking_is_deterministic`)*

## Attachments

- [ ] Upload a real image to an article; confirm it appears in the article's attachment list with a working URL. *(automated: `tests_attachments.py`)*
- [ ] Rename a non-image file to `.png` and upload it; confirm it's rejected (signature sniffing, not extension trust). *(automated: `tests_attachments.py::test_attachment_validation_rejects_fake_image`)*
- [ ] As an operator in workspace B, try to attach a file to workspace A's article by ID; confirm 404. *(automated: `tests_attachments.py::test_cross_workspace_attachment_denied`)*

## Sharing into a conversation

- [ ] Inside an active conversation, click the 📚 button, search, and "ارسال کارت مقاله" (send article card); confirm the customer widget shows a real message card with title/excerpt/link, live (no reload needed). *(automated: E2E `knowledge-base.spec.ts`)*
- [ ] Edit the shared article's title afterward; confirm the already-sent message in the conversation history still shows the OLD title (frozen snapshot, not a live view). *(automated: `tests_sharing.py::test_changed_article_does_not_alter_old_message_snapshot`)*
- [ ] Refresh the customer widget after receiving an article card; confirm it's still there. *(automated: E2E)*

## Feedback

- [ ] As a visitor (with a real session token), submit "helpful"; submit again as "not helpful" with a comment; confirm exactly one feedback row exists and the second submission won. *(automated: `tests_feedback.py::test_duplicate_feedback_is_deterministic_upsert`)*
- [ ] As an operator, view an article's feedback summary; confirm counts match. *(automated: `tests_feedback.py::test_feedback_summary_endpoint`)*

## Macros — permissions and visibility

- [ ] As `admin@ws.com`, create a WORKSPACE-visibility macro; confirm it starts inactive. *(automated: `tests_permissions.py`, E2E)*
- [ ] As `operator@ws.com`, try to create a WORKSPACE-visibility macro; confirm it's refused (403), but a PRIVATE one for yourself succeeds. *(automated: `tests_permissions.py`)*
- [ ] As the PRIVATE macro's owner, edit its actions; as a *different* operator in the same workspace, confirm the macro is invisible (404, not 403). *(automated: `tests_permissions.py`)*
- [ ] As the PRIVATE macro's owner, try to PATCH `visibility: WORKSPACE`; confirm the field is silently ignored (still PRIVATE afterward) — no self-promotion. *(automated: `tests_permissions.py::test_operator_cannot_self_promote_private_macro_to_workspace`)*
- [ ] As a user who is Admin in Workspace A and only Operator in Workspace B, confirm you cannot create/manage a WORKSPACE macro in Workspace B. *(automated: `tests_permissions.py::test_mixed_role_cross_workspace_user_denied`)*

## Macro execution

- [ ] Open the ⚡ macro palette in a conversation, select an active macro; confirm the **preview** shows the resolved reply text (with real customer name substituted) and *nothing* is sent/changed until you click "تأیید و اجرا". *(automated: `tests_execution.py::test_preview_has_no_side_effects`, E2E)*
- [ ] Confirm execution; confirm every configured action actually happened exactly once (reply sent, tag added, priority changed, team transferred, status set). *(automated: `tests_actions.py`, E2E)*
- [ ] Click "تأیید و اجرا" and immediately click it again (or double-click fast); confirm only ONE execution happened (check `/macros/history`) — never a duplicated reply/tag/assignment. *(automated: `tests_execution.py::test_double_execution_is_idempotent`, E2E)*
- [ ] Configure a macro with one valid action followed by one action referencing a deleted resource; run it; confirm the status shows "نیمه‌موفق" and the history detail shows exactly which action failed and why. *(automated: `tests_execution.py::test_partial_failure_is_recorded`, E2E)*
- [ ] Fix the failing action's reference, then click "تلاش دوباره" (retry) on the same execution; confirm the already-succeeded action(s) are NOT re-run (spot-check: no duplicate reply/note) and the execution becomes SUCCEEDED. *(automated: `tests_execution.py::test_retry_does_not_duplicate_successful_actions`)*
- [ ] Run a macro with `CREATE_INTERNAL_NOTE`; confirm the note appears in the operator view but never in the customer widget, even after a reload. *(automated: `tests_actions.py::test_internal_note_remains_private`, E2E)*
- [ ] Set an agent's capacity to 0 (or fill it up); run a macro that `ASSIGN_TO_AGENT`s that agent; confirm the action fails cleanly (visible error, conversation stays unassigned) — capacity is never bypassed. *(automated: `tests_actions.py::test_assignment_action_obeys_capacity`, E2E)*
- [ ] Try to execute another workspace's macro by ID via the API directly; confirm 404. *(automated: E2E `macros.spec.ts`)*
- [ ] On a narrow/mobile viewport, open a conversation, use the macro palette, and confirm it's usable (button reachable, palette readable, preview/confirm both tappable). *(automated: E2E mobile-viewport scenario)*

## Cross-cutting

- [ ] Confirm the existing Customer Chat, Platform Support, Team Operations, and Automation Engine flows are all unaffected — run their existing E2E suites and confirm they're still green. *(automated: full `npx playwright test` run)*
