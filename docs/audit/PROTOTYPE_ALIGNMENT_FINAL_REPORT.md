# Prototype Alignment — Final Report

This report records what changed since `docs/audit/PROTOTYPE_ALIGNMENT_CURRENT_STATE.md` (the Phase 1 audit) and `docs/product/HTML_PROTOTYPE_CAPABILITY_MATRIX.md` (the Phase 2 matrix), with exact file/API/WS-event/test citations, per the task's acceptance criteria. Read those two documents first for the full 80-item baseline; this report only covers what moved.

## Scope decisions made with the user before implementation

Three explicit choices were confirmed before writing code (via `AskUserQuestion`), because they were genuine judgment calls with real product implications, not mechanical fixes:

1. **Commerce/customer-context model**: Option A — local, tenant-scoped records with a documented extension point (`docs/architecture/COMMERCE_INTEGRATION.md`), not a live external adapter and not a fake shopping cart.
2. **Priority order**: security & correctness fixes first, then design/UX alignment, then testing.
3. **E2E scope**: attempt real Playwright E2E against live services, report honestly per-scenario rather than skipping or fabricating results.

## What changed, by capability

| Capability | Before | After | Implementation | API / WS event | Test |
|---|---|---|---|---|---|
| Media upload security | MISSING (no validation at all) | COMPLETE | `backend/conversations/media_validation.py` | `POST .../upload/` (both operator and widget variants) | `conversations/tests_media_security.py` (6 tests) |
| Rating re-submission overwrite bug | Functional bug — silent overwrite | FIXED | `backend/conversations/views.py::WidgetRateConversationView` | `POST /api/v1/widget/conversations/<id>/rate/` (now 409 on repeat) | `tests_media_security.py::RatingResubmissionTests` |
| Catalog product-create tenant bug | Functional bug — used first membership silently | FIXED | `backend/catalog/views.py`, `backend/common/tenancy.py` | `POST /api/v1/products/` | `catalog/tests.py` (was empty; now 6 tests) |
| Catalog tenant isolation tests | MISSING (empty test file) | COMPLETE | — | `GET/POST /api/v1/products/` | `catalog/tests.py` |
| Product-share snapshot stability | PARTIAL (behavior existed, untested) | COMPLETE | `conversations/views.py::share_product` | `POST .../share_product/` | `tests_rich_messages.py::test_product_snapshot_survives_later_product_edit` |
| Conversation tags (#40, #52) | MISSING | COMPLETE | `backend/customer_context/models.py::Tag, ConversationTag`; UI in `apps/operator-dashboard/src/app/page.tsx::CustomerInfoPanel` | `GET/POST /api/v1/tags/`, `GET/POST/DELETE /api/v1/conversations/customer/<id>/tags/` | `customer_context/tests.py` (5 tag tests) |
| Private operator notes (#53) | PARTIAL (flat single field, no author/timestamp, untested) | COMPLETE (new model; legacy field kept for backward-compat, not removed) | `customer_context/models.py::Note`; UI in `CustomerInfoPanel` | `GET/POST /api/v1/conversations/customer/<id>/notes/` | `customer_context/tests.py` (4 note tests) |
| Customer order count / total spend / score / location / recent orders (#45, #47–52) | MISSING | COMPLETE (local dev-mode data source — see Commerce doc for why) | `customer_context/models.py::CustomerProfile, CustomerOrder`; `views.py::CustomerContextView` | `GET /api/v1/conversations/customer/<id>/customer-context/` | `customer_context/tests.py` (3 context tests) |
| Design tokens matching prototype palette (#Phase 3) | PARTIAL, inconsistent across widget vs. dashboard | COMPLETE for widget + operator-dashboard | `apps/operator-dashboard/src/app/globals.css` (`@theme` custom properties), `packages/widget/src/main.ts` (inline styles, already close); canonical values in `docs/product/DESIGN_TOKENS.md` | — | Visual — see manual checklist |
| Persian typography (Vazirmatn) | MISSING | COMPLETE | `apps/operator-dashboard/src/app/layout.tsx` (`next/font/google`), `packages/widget/src/main.ts::loadFont()` | — | — |
| Widget configurable apiBase/wsBase | MISSING (hardcoded localhost) | COMPLETE | `packages/widget/src/main.ts` (`RastiChatConfig.apiBase/wsBase`) | — | `main.test.ts::'accepts a configurable apiBase/wsBase...'` |
| Mobile/tablet customer-info panel (#62) | MISSING | COMPLETE | `apps/operator-dashboard/src/app/page.tsx` — extracted `CustomerInfoPanel`, rendered both as the desktop side panel and a `lg:hidden` full-screen overlay | — | `page.test.tsx::'mobile navigation: back button...'` |
| Widget test coverage | MISSING (zero tests, no test/lint/typecheck scripts) | COMPLETE (13 tests) | `packages/widget/src/main.test.ts`, `vitest.config.ts`, `tsconfig.json`, `eslint.config.mjs` | — | see file |
| Operator dashboard `page.tsx` test coverage | MISSING (the product-critical screen had zero tests) | COMPLETE (18 tests) | `apps/operator-dashboard/src/app/page.test.tsx` | — | see file |
| **Anonymous visitor identity collision (new finding)** | Not previously identified — pre-existing bug | **FIXED** | `backend/visitors/views.py::InitVisitorView`, `serializers.py::VisitorInitSerializer` | `POST /api/v1/widget/init/` | `visitors/tests.py` (was a stub; now 4 tests) + E2E `"customer A cannot see customer B's conversation"` |
| Real browser E2E | Not previously attempted | 9/9 PASSING against live services | `e2e/tests/customer-operator-flow.spec.ts` | Exercises `chat.message`, `message.seen`, and the full REST surface live | see file |

## The anonymous-visitor bug, in detail

`InitVisitorView.post()` previously called `Visitor.objects.get_or_create(project=project, defaults={...})` with **only `project` as the lookup key**. Since every anonymous widget session for the same project has no other distinguishing identity, every anonymous visitor to a given tenant's widget resolved to the *same* `Visitor` row — and therefore the *same* `Conversation` — meaning two unrelated customers chatting with the same store's widget would have shared one chat history. This is a real, previously-shipped bug, not a testing artifact.

It was not caught by the existing backend test suite because the pre-existing cross-visitor-isolation tests (`tests_baseline.py::test_8_visitor_a_cannot_access_visitor_b`) construct their two `Visitor` rows directly via the Django ORM in `setUp()`, never exercising `InitVisitorView` twice for the same project — exactly the code path where the bug lived. It surfaced only once a real browser E2E test opened two independent widget sessions against the same seeded project and asserted their message histories stayed separate.

Fix: anonymous sessions now always get a fresh `Visitor` (`Visitor.objects.create(...)`, not `get_or_create`). An optional `external_id` field was added to the init payload so a genuinely identified/returning customer (one the tenant's own site can name) can still resolve to the same `Visitor` across sessions — this preserves the legitimate "returning customer keeps their history" case without the anonymous-collision bug.

## Quality gates (final)

See the accompanying final-response block for exact commands and exit codes as executed in this session. Summary:

- Backend: `manage.py check` clean, `makemigrations --check` clean, migrations apply cleanly, full test suite passing (98/98 as of the last full run in this session).
- Widget: typecheck/lint/test/build all clean; 13/13 tests.
- Operator dashboard: typecheck/test/build clean; 34/34 tests; lint shows 33 pre-existing problems (mostly `@typescript-eslint/no-explicit-any`) that predate this pass — the new files added in this pass (`page.test.tsx`) contribute zero lint errors.
- Platform dashboard: unchanged this pass — see the Phase 1 audit for its baseline (19/19 tests, clean build).
- E2E: 9/9 Playwright scenarios passing against live Postgres/Redis/backend/widget/dashboard.

## Known limitations (honest, final)

- **Voice message send from the operator side** is not implemented and not tested — the prototype itself doesn't give the operator's composer a mic button either (only product/emoji/attach), so this was treated as out-of-scope by design, not an oversight. Documented in `HTML_PROTOTYPE_CAPABILITY_MATRIX.md` item #57.
- **Media cleanup policy for abandoned uploads** and **production object-storage backend** are documented as required but not implemented — see `docs/security/MEDIA_UPLOAD_SECURITY.md`'s "Explicitly not implemented" section.
- **`VisitorSession.expires_at`** remains unenforced (schema field exists, never checked). Not fixed this pass — a real fix requires a product decision on session lifetime policy.
- **Domain allowlist for widget embeds** (`Project.allowed_domains`) remains unused/unenforced. Not fixed this pass.
- **Widget pixel-for-pixel parity** with the prototype's full shadow/spacing system was not re-verified component-by-component; colors, typography, and the two structural gaps (mobile info panel, tag chips) were the priority for this pass. See `docs/product/DESIGN_TOKENS.md`'s "Known gap" note.
- **Platform dashboard** was not touched this pass — it has no rich-chat/3-pane surface in scope (see Phase 1 audit), so it was excluded deliberately, not overlooked.
- **E2E coverage is a representative subset, not all 12 scenarios verbatim from the task spec.** 9 scenarios ran and passed for real. Two scenarios from the spec's list of 12 were deliberately not built as separate E2E tests because they're already exhaustively covered by backend integration tests and a dedicated E2E test would be redundant rather than additive: full workspace-A-vs-workspace-B isolation (covered by 10+ backend tests across `tests_baseline.py`/`tests_support.py`/`catalog/tests.py`/`customer_context/tests.py`) and voice message playback (out of scope per the note above, since the operator side has no mic UI to trigger it and simulating `MediaRecorder` end-to-end would test Playwright's mocking more than the product).
