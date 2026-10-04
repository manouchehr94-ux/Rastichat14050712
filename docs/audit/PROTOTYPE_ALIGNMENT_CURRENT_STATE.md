# Prototype Alignment — Current Repository State

Audit date: 2026-08-04
Reference prototype: `index (3)(1).html` (Persian RTL customer widget + operator/admin dashboard, single static HTML file, mock data only)
Baseline verified by actually running the suites in this session (not taken on faith from prior docs):

- `python manage.py check` → **0 issues**
- `python manage.py makemigrations --check --dry-run` → **no changes detected**
- `python manage.py migrate` → **clean, 29 migrations applied**
- `python manage.py test --verbosity=2` → **66/66 tests passed** (the previously-committed `PHASE1_BASELINE.txt` claimed "50 Passed"; the real, currently-executed count is 66 — the repo has grown since that doc was written and it is now stale)
- `packages/widget`: `npm ci` + `npm run build` → clean IIFE build, no test/lint/typecheck scripts exist in this package at all
- `apps/operator-dashboard`, `apps/platform-dashboard`: quality gates run separately, results folded into the matrix below

This document classifies every capability visible in the prototype against the real repository at `/home/user/RastiChatTaasisat`. Classification legend: **COMPLETE**, **PARTIAL**, **MOCK_ONLY**, **MISSING**, **BROKEN**, **OUT_OF_SCOPE_WITH_REASON**.

## Repository hygiene note (flagged, not acted on)

`apps/operator-dashboard/AGENTS.md` and `apps/platform-dashboard/AGENTS.md` each contain an instruction telling an agent to read `node_modules/next/dist/docs/` before writing code, framed as "this is not the Next.js you know." No such non-standard Next.js exists — `package.json` pins ordinary `next@16.2.12`. This reads as planted/injected content rather than genuine project documentation. It was not followed. Recommend the repository owner review and remove it; out of scope for this audit to silently delete since it wasn't the audit's mandate, but it must not be treated as a legitimate constraint by any future agent.

## 1. Backend domain model (`backend/`)

| Area | Status | Evidence |
|---|---|---|
| Rich message types (text/image/voice/product/rating_request/rating) | **COMPLETE** | `conversations/models.py:47-53` `Message.MessageType`; exercised end-to-end in `conversations/tests_rich_messages.py` |
| Server-resolved sender identity (never trusts client) | **COMPLETE** | `views.py:169,300,352`; `consumers.py:63-74,114-125`; regression tests `tests_baseline.py:119-143`, `tests_support.py:113-118` |
| Message idempotency (`client_message_id`) | **COMPLETE** | DB-level `unique_together` `models.py:68`; enforced in every write path; tested `tests_baseline.py` `test_19`, `test_20`, `tests_support.py` `test_18` |
| Read receipts | **COMPLETE** | `MessageReceipt` model `models.py:71-75`; `message.seen` WS event `consumers.py:29-30`; tested `tests_rich_messages.py` (`operator_mark_read…`, `visitor_mark_read…`) |
| Typing indicator | **COMPLETE** | `typing.indicator` WS event with self-echo suppression `consumers.py:23-27,55,106`; tested `tests_rich_messages.py::TypingAndSeenWebsocketTests` |
| Tenant isolation (visitor/workspace/platform) | **COMPLETE** | Queryset-level scoping throughout `conversations/views.py`, `catalog/views.py`; WS-level scoping in all three consumers; 10+ dedicated cross-tenant-denial tests across `tests_baseline.py` and `tests_support.py` |
| Product catalog (tenant-scoped) | **PARTIAL** | Model exists `catalog/models.py:6-24` with brand/name/price/old_price/rating/reviews_count/image/is_active, tenant-scoped queryset `catalog/views.py:11-12`. Missing: `currency` field, `external_id`, explicit availability field distinct from `is_active`. `perform_create` uses the requesting user's *first* workspace membership (`views.py:14-16`) rather than an explicit workspace, a latent multi-workspace-operator bug. **`backend/catalog/tests.py` is empty — zero dedicated unit tests for the catalog app itself.** |
| Product-share snapshot (historical stability) | **PARTIAL** | De-facto snapshot achieved by copying a plain dict into `Message.metadata` at share time (`catalog`-adjacent logic in `conversations/views.py:93-117` `share_product`) — historical messages *are* stable against later product edits, but there is no explicit schema/versioning for the snapshot, so it's implicit behavior rather than a documented contract |
| Rating request/submission | **PARTIAL** | Request + submit flow works and is authorization-correct (`views.py:119-131,239-262`; validated 1-5, tested `tests_rich_messages.py`). **Gap**: re-submitting a rating with the same conversation overwrites `conversation.rating` unconditionally (`views.py:260-261`) with no "already rated" guard — a visitor can re-rate indefinitely, only the duplicate *message* is blocked, not the duplicate *rating value change* |
| Customer context / CRM layer (phone, location, since-date, order history, spend, score, tags) | **MISSING** | `Visitor` model has only name/email/mobile/metadata (`visitors/models.py:5-16`); no `Order`, `Tag`, or customer-stats model anywhere in the codebase |
| Private operator notes | **PARTIAL** | Only a single flat `Conversation.notes` text field (`conversations/models.py:36`), writable by any `IsWorkspaceOperator` via the generic `ModelViewSet` update path — no discrete note entries, no author/timestamp per note, no test coverage found referencing `notes` |
| Conversation/customer tags | **MISSING** | No `tags` field or model exists anywhere in the backend |
| Media upload security | **PARTIAL → risk flag** | Upload endpoints exist and correctly gate by conversation ownership + `client_message_id` idempotency (`views.py:63-91,204-237`), but there is **no** extension allowlist, MIME/content-type check, file-signature check, or enforced max-size (`FILE_UPLOAD_MAX_MEMORY_SIZE` only controls memory-vs-disk buffering, not a size cap). Media is served via Django's dev-only `static()` helper (`config/urls.py:13-14`) — nothing serves `/media/` when `DEBUG=0`, i.e. no production storage backend is wired up at all |
| Visitor session expiry | **PARTIAL** | `VisitorSession.expires_at` field exists in the schema (`visitors/models.py`) but is never read or enforced anywhere — sessions are effectively permanent |
| Domain allowlist for widget embeds | **PARTIAL** | `Project.allowed_domains` field exists (`projects/models.py:9`) but is unused by `InitVisitorView` or CORS config — any origin can currently initialize a widget session for any known `public_key` |

## 2. Widget (`packages/widget`)

| Area | Status | Evidence |
|---|---|---|
| Real WS connect / history / reconnect / dedup | **COMPLETE** | `main.ts:336-367` (connect + reconnect), `323-334` (history fetch), `59,490-494` (dedup via `client_message_id`) |
| Text / image / voice / product / rating rendering | **COMPLETE** | `renderMessage()` `main.ts:534-578` |
| Real image/voice upload | **COMPLETE** | `uploadFile()` `main.ts:418-436`; `MediaRecorder`-based capture `main.ts:438-470` |
| Typing events, read receipts | **COMPLETE** | `sendTyping()` `main.ts:384-391`; `sendMarkRead()` `main.ts:393-397` |
| Emoji picker, quick replies | **COMPLETE** | `main.ts:214,240-254` (emoji); `216-223` (quick replies) |
| RTL | **COMPLETE** | `main.ts:86` |
| Persian typography (Vazirmatn, per prototype) | **MISSING** | `main.ts:85` hardcodes `Tahoma, Arial, sans-serif` — no Vazirmatn or any webfont load |
| Design tokens matching prototype palette | **PARTIAL** | Colors are close to the prototype (terracotta `#BC5A38`, cream `#FAF3EA`, gold `#C2954A`, green `#5E8A56`) but hardcoded inline inside one `<style>` template literal in `main.ts:84-181`, not sourced from a shared/reusable tokens file, and fully overridable per-embed via `config.primaryColor` with no palette guardrails |
| Mock data in shipped source | **MISSING (good — none found)** | No hardcoded conversations/products/customers in `main.ts`; `EMOJIS`/`QUICK_REPLIES` are UI copy, not business data |
| Automated tests | **MISSING** | Zero `*.test.ts` files, no test runner configured, no `test`/`lint`/`typecheck` npm scripts at all |
| Configurable API/WS base per deployment | **MISSING** | `apiBase`/`wsBase` hardcoded to localhost (`main.ts:56-57`), not derived from `config` or env — the widget cannot currently be pointed at a non-local backend without a source edit |

## 3. Operator dashboard (`apps/operator-dashboard`)

There are two distinct chat surfaces in this app, which itself is an audit finding: `src/app/page.tsx` (the real customer-chat 3-pane dashboard, matches the prototype's intent) and `src/app/support/page.tsx` (a separate, visually unrelated workspace→platform support-ticket screen). Findings below are for `page.tsx` unless noted.

| Area | Status | Evidence |
|---|---|---|
| Conversation list, search, status tabs, unread badges | **COMPLETE** | `page.tsx:380-402,415,419` |
| Rich message rendering (text/image/voice/product/rating) | **COMPLETE** | `MessageBubble()` `page.tsx:100-156`, `VoiceBubble()` `75-98` |
| Customer-info panel (profile, stats, notes) | **PARTIAL** | Present (`page.tsx:519-554`) but **desktop-only** (`hidden lg:flex`, line 520) — no tablet/mobile overlay equivalent exists at all, which is a direct gap against the prototype's mobile info-overlay requirement |
| Quick replies, emoji picker, product picker/share | **COMPLETE** | `471-475` (quick replies), `477-483` (emoji), `484-498` + `lib/api.ts:99-107` (product share) |
| Typing state, read receipts (realtime) | **COMPLETE** | typing dots `460-466`; seen ticks `104,151-153` driven by `message.seen` WS events `216-220` |
| Mobile list↔chat navigation | **COMPLETE** | `mobileView` state `174`, conditional `hidden md:flex` on both panes `390,429`, back button `434` |
| Mobile/tablet customer-info access | **MISSING** | No mechanism to reach the info panel below the `lg` breakpoint — the prototype's "info overlay with back control" pattern does not exist here |
| Tags | **MOCK_ONLY-adjacent / MISSING** | Only a single free-text "category" field (`542-546`) — no multi-tag chip UI, matching the backend's lack of a tags model |
| Design tokens matching prototype palette | **PARTIAL, inconsistent** | Approximated via scattered Tailwind utility classes (`orange-600`, `amber-*`, `green-600`) rather than centralized tokens; Tailwind `orange-600` (`#EA580C`) does not match the widget's actual terracotta (`#BC5A38`) — the two production surfaces currently render two different "terracotta" oranges |
| Persian typography | **MISSING** | `layout.tsx:11` uses `font-sans` → Geist (Latin font), no Vazirmatn |
| Shared component primitives (Avatar/Chip/Badge/Button) | **MISSING** | Only `MessageBubble`/`VoiceBubble` are factored out; avatar/chip/badge markup is duplicated inline at each call site, and not shared with `support/page.tsx` or `platform-dashboard` at all |
| Automated tests for the real dashboard | **MISSING** | `src/app/support/page.test.tsx` covers only the *unrelated* support-ticket screen; **`page.tsx` — the product-critical 3-pane screen — has zero test coverage** |
| Mock data in shipped source | **MISSING (good — none found)** | All conversation/customer data is fetched from the real API; static arrays are UI copy only |

## 4. Platform dashboard (`apps/platform-dashboard`) — regression scope only

This app is a support-ticket inbox (`src/app/inbox/page.tsx`), structurally parallel to the operator dashboard's `support/page.tsx`, and has **no** counterpart to the prototype's rich customer-chat 3-pane screen. It is therefore not directly in scope for most prototype capabilities, but is in scope for regression-safety (Phase 12) since it shares the same backend. Text-only bubbles, indigo color scheme, RTL present, no Persian font (same gap as operator-dashboard), reasonably well-tested (21 tests across `inbox/page.test.tsx` + `lib/api.test.ts`).

## 5. Cross-cutting system requirements

| Area | Status | Evidence |
|---|---|---|
| WebSocket event correctness (server-resolved identity, tenant-scoped groups, safe serialization) | **COMPLETE** | See backend table above |
| Message ordering / pagination | **PARTIAL** | History pagination exists and is tested for the support channel (`tests_support.py` `test_22_history_pagination`) but was not separately confirmed for the customer-widget history endpoint in this pass — needs an explicit test before being marked COMPLETE |
| Media security (extension/MIME/signature/size validation) | **MISSING** | See backend table — this is the single largest security gap found in the audit |
| No cross-workspace data exposure | **COMPLETE** | Extensively tested on both REST and WS paths |
| Auditability | **PARTIAL** | An `audit` app exists (`backend/audit/`) and is exercised by `tests_support.py::test_28_audit_events`, but only for the support-escalation flow — not for customer-chat rich-message actions (uploads, product shares, ratings) |

## 6. Out of scope (documented, not silently dropped)

| Item | Reason |
|---|---|
| Full shopping-cart / checkout persistence | The prototype's "افزودن به سبد خرید" (add to cart) button is decorative in the mock; the repository owns no commerce/checkout system today, and the task instructions explicitly forbid building a fake cart to imitate the prototype. Recommended treatment: secure product-URL redirect / integration contract only (see `docs/architecture/COMMERCE_INTEGRATION.md`, to be authored in Phase 6) |
| Voice/video calling (prototype's phone-icon button) | Decorative in the prototype (no wiring in its own JS); no backend signaling infrastructure exists; treated as UI-only affordance, not a real capability, until product explicitly requests it |

## Summary counts

- COMPLETE: 20
- PARTIAL: 12
- MISSING: 8
- MOCK_ONLY (production code depending on prototype-style hardcoded data): **0 found** — this is a genuinely clean result; neither the widget nor the operator dashboard ship hardcoded sample conversations/products/customers in non-test source
- OUT_OF_SCOPE_WITH_REASON: 2

The repository is in materially better shape than "greenfield against the prototype" — the hard, security-sensitive parts (tenant isolation, sender-identity trust, idempotency, realtime correctness) are already COMPLETE and test-covered. The real gaps are concentrated in: (1) media upload security, (2) the customer-context/CRM layer (phone/location/orders/spend/tags) which is entirely absent, (3) design-token centralization and Persian typography, (4) mobile/tablet customer-info access in the operator dashboard, and (5) test coverage for the dashboard's primary screen and the widget as a whole.
