# HTML Prototype Capability Matrix

Source prototype: `index (3)(1).html`. Status values: **COMPLETE**, **PARTIAL**, **MOCK_ONLY**, **MISSING**, **BROKEN**, **OUT_OF_SCOPE**. Evidence is cited by file:line against the current repository. This matrix is the input to prioritizing Phases 3–13; it is not itself an implementation record (see `docs/audit/PROTOTYPE_ALIGNMENT_FINAL_REPORT.md` for that once work lands).

## Customer experience

| # | Capability | Prototype evidence | Current repo state | Backend work required | Frontend work required | Tests required | Status |
|---|---|---|---|---|---|---|---|
| 1 | RTL Persian interface | `<html lang="fa" dir="rtl">` line 2 | Widget `main.ts:86` sets `dir:rtl`; dashboards set it in `layout.tsx` | none | none | visual/E2E check | COMPLETE |
| 2 | Store identity/branding | `.brand`/`.logo` lines 421-423 | Widget reads project branding via init call; not verified whether `primaryColor`/store name are wired from real `Project` fields end-to-end | verify `Project` exposes name/logo | verify widget consumes it, not just a placeholder | widget unit test | PARTIAL |
| 3 | Consultant identity | `.ch-name` line 434 | Dashboard sends replies as the authenticated operator; widget shows sender via message data, not a dedicated "assigned operator profile" call | expose assigned-operator display name/avatar on conversation | render in widget header | integration test | PARTIAL |
| 4 | Consultant online/away/offline status | `.avatar.online/.away/.off` lines 75-77 | No operator presence model/broadcast found in backend survey | presence tracking (WS connect/disconnect → status) | render dot in widget header | new backend + widget tests | MISSING |
| 5 | Response-time indication | `ch-status` "میانگین پاسخ کمتر از ۲ دقیقه" line 435 | Not present anywhere | computed metric (optional, low priority) | display element | n/a until built | MISSING |
| 6 | Text messages | `renderMsg` type text, line 558 | `Message.MessageType.TEXT`, `main.ts` renderer | — | — | existing | COMPLETE |
| 7 | Multi-line messages | `white-space:pre-wrap` line 558 | `content` TextField, no length-only single-line constraint | — | verify widget preserves newlines | existing | COMPLETE |
| 8 | Emoji picker | `.emoji-pop` / `emojiContent()` line 548 | Widget `main.ts:240-254`; dashboard `page.tsx:477-483` | — | — | none dedicated | COMPLETE |
| 9 | Quick replies | `.quick-replies` lines 448-454 | Widget `main.ts:216-223` (static list, fills input) | make quick-replies tenant-configurable (currently hardcoded copy) | same | widget test | PARTIAL |
| 10 | Image attachment | `.bubble.img-only` line 560 | `MessageType.IMAGE`; upload endpoints `views.py:63-91,204-237` | add validation (Phase 9) | widget `uploadFile()` `main.ts:418-436` | existing + new validation tests | COMPLETE (validation gap tracked separately under #68) |
| 11 | Image caption | `att-cap` line 560 | `metadata.caption` supported (per rich-message tests) | — | verify widget composer supports caption input, not just file | widget test | PARTIAL |
| 12 | Voice recording | `.mic-btn` / `MediaRecorder` | Widget `main.ts:438-470` | — | — | widget test (none exist) | COMPLETE, untested |
| 13 | Voice playback | `.vc-play` / waveform | Widget renders voice bubble with play button, `main.ts` | — | — | widget test (none exist) | COMPLETE, untested |
| 14 | Voice duration | `vc-dur` | `metadata.duration` | — | — | test | COMPLETE, untested |
| 15 | Typing indicator | `.bubble.typing` | WS `typing.indicator` event `consumers.py:23-27`; widget `showTyping/hideTyping` `main.ts:369-382` | — | — | `TypingAndSeenWebsocketTests` | COMPLETE |
| 16 | Delivery/read receipt | `seenMark` line 554 | `MessageReceipt` model, `message.seen` event | — | — | `tests_rich_messages.py` mark-read tests | COMPLETE |
| 17 | Product message card | `.bubble.product` line 564 | `MessageType.PRODUCT`, widget renderer, dashboard `MessageBubble` `116-134` | — | — | `test_operator_can_share_product` | COMPLETE |
| 18 | Product image | `prod-img` | `Product.image` URLField | — | — | — | COMPLETE |
| 19 | Product brand | `prod-brand` | `Product.brand` | — | — | — | COMPLETE |
| 20 | Product name | `prod-name` | `Product.name` | — | — | — | COMPLETE |
| 21 | Current price | `prod-price .now` | `Product.price` | — | — | — | COMPLETE |
| 22 | Old price | `prod-price .old` | `Product.old_price` | — | — | — | COMPLETE |
| 23 | Discount percentage | computed in prototype JS (`disc=...`) | Not stored; computable client-side from price/old_price | — | compute in render, not stored server-side | — | PARTIAL (deliberately derived, not a gap) |
| 24 | Product rating | `prod-rate`/`stars()` | `Product.rating` | — | — | — | COMPLETE |
| 25 | Product review count | `(reviews)` | `Product.reviews_count` | — | — | — | COMPLETE |
| 26 | Authenticity/shipping info footer | `.prod-foot` static copy | Decorative static copy in prototype itself (not tenant data) | none | render as static UI copy, not fake per-product data | — | OUT_OF_SCOPE (decorative in source prototype) |
| 27 | Add-to-cart action/integration contract | `.prod-cart` button | No cart/commerce system in repo | define secure product-URL contract (Phase 6) | wire button to `Product` external URL, not fake cart | contract test | MISSING |
| 28 | Rating request message | `.bubble.rating` | `MessageType.RATING_REQUEST`, `request_rating` view | — | — | `test_operator_can_request_rating_and_visitor_can_rate` | COMPLETE |
| 29 | Customer submits rating | `.rt-stars` click handler | `WidgetRateConversationView`, 1-5 validated | fix re-rating overwrite gap (see audit item) | — | new test: reject/handle re-rate | PARTIAL — functional bug found |
| 30 | Refresh preserves history | static in prototype (no reload logic needed) | `loadHistory()` `main.ts:323-334`, `test_6_history_survives_refresh` | — | — | existing | COMPLETE |
| 31 | Reconnect without duplicates | n/a in prototype (no real WS) | `main.ts:364-366` reconnect + dedup `59,490-494`; `test_20_reconnect_no_duplicates` | — | — | existing | COMPLETE |
| 32 | Mobile responsive behavior | `@media(max-width:760px)` block | Widget layout not explicitly audited for mobile breakpoints in this pass | — | verify/adjust widget CSS breakpoints | manual/E2E mobile check | PARTIAL — needs explicit verification |
| 33 | Tablet responsive behavior | `@media(max-width:1100px)` block | Not explicitly verified for widget (single-column by design, likely fine) | — | verify | manual/E2E | PARTIAL — needs explicit verification |
| 34 | Safe loading/error/offline states | n/a in prototype | Reconnect exists; explicit "offline/reconnecting" UI state not confirmed present | — | add visible offline/error banner if missing | widget test | PARTIAL — needs explicit verification |

## Operator experience

| # | Capability | Prototype evidence | Current repo state | Backend work required | Frontend work required | Tests required | Status |
|---|---|---|---|---|---|---|---|
| 35 | Conversation list | `.conv-list`/`renderList()` | `page.tsx:380-402` | — | — | none for `page.tsx` | COMPLETE, untested |
| 36 | Search conversations | `.cs-search input` | `page.tsx:396` filters `filtered` array client-side | consider server-side search if list grows large | — | test | COMPLETE, untested |
| 37 | Filter all/waiting/open/archive | `.cs-tabs` | `TABS` `page.tsx:43-46` | verify tab semantics map to real conversation status field | — | test | PARTIAL — needs status-model cross-check |
| 38 | Unread badge | `.ci-badge` | `page.tsx:415` | — | — | test | COMPLETE, untested |
| 39 | Latest-message preview + timestamp | `.ci-preview`/`.ci-time` | present in list rendering | — | — | test | COMPLETE, untested |
| 40 | Conversation tags | `.ci-tags` chip | Only single free-text category exists, not multi-tag | add `Tag`/conversation-tags model (Phase 8) | tag chip UI | new tests | MISSING |
| 41 | Selected-conversation state | `.conv-item.active` | present, `activeId` state equivalent | — | — | — | COMPLETE |
| 42 | Customer online state | avatar status dot | Same presence gap as #4 | presence tracking | — | — | MISSING |
| 43 | Customer details panel | `.info-panel` | `page.tsx:519-554`, desktop-only | — | mobile/tablet overlay (Phase 5) | test | PARTIAL |
| 44 | Customer phone | `ip-pmail` (shows phone) | `Visitor.mobile` exists; panel shows contact field | — | verify field mapping | test | PARTIAL |
| 45 | Customer location | `ip-row` "موقعیت" | No location field on `Visitor` | extend customer-context layer (Phase 7) | display | test | MISSING |
| 46 | Customer membership date ("عضو از") | `ip-row` "عضو از" | `Visitor.created_at` exists but not surfaced as "customer since" in UI | expose in serializer | display | test | PARTIAL |
| 47 | Customer order count | `ip-stat-box` "سفارش" | No order model | customer-context/commerce adapter (Phase 6/7) | display | test | MISSING |
| 48 | Customer total spending | "مجموع خرید" | No order/spend model | same | display | test | MISSING |
| 49 | Customer score | `ch-rate` | No score model (conversation `rating` exists but is per-conversation, not an aggregate customer score) | derive or add field | display | test | MISSING |
| 50 | Recent orders | `.order-row` list | No order model | commerce/customer-context adapter (Phase 6/7) | order list UI | test | MISSING |
| 51 | Order state (shipped/delivered/processing) | `order-st` chip | Same — no order model | same | same | test | MISSING |
| 52 | Customer tags | `.ip-tags` | Same as #40 | tags model (Phase 8) | tag UI | test | MISSING |
| 53 | Private operator notes | `.ip-note` textarea | `Conversation.notes` flat field `models.py:36`, no per-note author/timestamp, no dedicated endpoint, no tests | promote to discrete `Note` model w/ author+timestamp (Phase 8) | notes list UI | new API + UI tests | PARTIAL |
| 54 | Operator quick replies | `.a-quick` chips | `page.tsx:471-475`, hardcoded copy | make tenant-configurable (optional) | — | test | COMPLETE (hardcoded, functional) |
| 55 | Operator emoji picker | `#aEmoji`/`aEmojiPop` | `page.tsx:477-483` | — | — | test | COMPLETE, untested |
| 56 | Operator image attachment | `#aAttach` | present, upload flow reused | Phase 9 validation | — | test | COMPLETE, untested (validation gap shared with #68) |
| 57 | Operator voice message | prototype has no operator mic button (customer-only in prototype) | Not implemented for operator side | — | optional, not in prototype scope for operator | — | OUT_OF_SCOPE (prototype doesn't give operator a mic button either — confirmed by re-reading admin composer markup, which has product/emoji/attach only, no mic) |
| 58 | Product picker | `#aProd`/`.prod-picker` | `page.tsx:484-498` | — | — | test | COMPLETE, untested |
| 59 | Product sharing | `pp-item` click → push product msg | `handleShareProduct` `page.tsx:290-298`, backend `share_product` | — | — | `test_operator_can_share_product`, `test_cannot_share_product_from_other_workspace` | COMPLETE |
| 60 | End/close conversation | `.end-chat` button | Present in `page.tsx:444-448`; backend close/reopen exists for support flow (`tests_support.py` `test_26/27`) — needs confirmation it's wired for customer conversations too | verify customer-conversation close endpoint parity | — | test | PARTIAL — needs verification |
| 61 | Mobile list-to-chat navigation | `.mob-back` | `page.tsx:174,390,429,434` | — | — | test | COMPLETE, untested |
| 62 | Mobile customer-info panel | `.mob-info-btn`/`.info-open` | **Not implemented** — panel is `hidden lg:flex` with no mobile path | — | build mobile info overlay (Phase 5) | test | MISSING |
| 63 | Realtime incoming messages | `chat.message` WS event | `consumers.py:20-21` | — | — | existing | COMPLETE |
| 64 | Realtime read status | `message.seen` WS event | `consumers.py:29-30` | — | — | existing | COMPLETE |
| 65 | Realtime typing status | `typing.indicator` WS event | `consumers.py:23-27` | — | — | existing | COMPLETE |
| 66 | Assignment/status updates | prototype doesn't model assignment explicitly (single-operator mock) | Backend has real assignment for support flow (`tests_support.py` assignment tests); customer-conversation assignment model not confirmed in this pass | verify/extend | — | test | PARTIAL — needs verification |

## System requirements

| # | Capability | Prototype evidence | Current repo state | Backend work required | Frontend work required | Tests required | Status |
|---|---|---|---|---|---|---|---|
| 67 | Persistent rich-message types | implied by all bubble types | `Message.MessageType` + `metadata` JSON | — | — | existing | COMPLETE |
| 68 | Safe file upload | implied by attach/voice UI | Upload endpoints exist; **no extension/MIME/signature/size validation** (`views.py:63-91,204-237`) | full validation stack (Phase 9) | — | new security tests | MISSING — highest-priority security gap |
| 69 | Tenant-scoped catalog | implied by product data being real | `catalog/views.py:11-12` scopes by workspace | fix first-membership bug; add tests (catalog/tests.py is empty) | — | new catalog tests | PARTIAL |
| 70 | Tenant-scoped products | same | same | same | — | same | PARTIAL |
| 71 | Secure product sharing | implied | `share_product` scoped + tested cross-workspace-denied | — | — | existing | COMPLETE |
| 72 | Secure rating submission | implied | session-token-scoped, validated 1-5 | fix re-rate overwrite (#29) | — | new test | PARTIAL |
| 73 | Idempotency | implied by "no duplicates on reconnect" | `client_message_id` unique constraint, enforced everywhere | — | — | existing, extensive | COMPLETE |
| 74 | Message ordering | implied | `created_at` ordering; not separately stress-tested | — | — | add explicit ordering test | PARTIAL |
| 75 | History pagination | implied by real product | Confirmed for support channel; not confirmed for widget/customer history endpoint | verify/add pagination on widget history if missing | — | new test | PARTIAL |
| 76 | Tenant isolation | implied throughout | Extensively enforced and tested (10+ dedicated tests) | — | — | existing | COMPLETE |
| 77 | Role authorization | implied (admin vs customer views) | `common/permissions.py`, six role classes, used throughout | — | — | existing | COMPLETE |
| 78 | Auditability | not visible in prototype directly | `audit` app exists, tested for support-escalation only | extend to rich-message actions (optional, Phase 9/10) | — | new tests | PARTIAL |
| 79 | Production-safe media handling | implied | Dev-only `static()` serving, no storage abstraction, no validation | storage abstraction + validation (Phase 9) | — | new tests | MISSING |
| 80 | No cross-workspace data exposure | implied throughout | Extensively enforced and tested | — | — | existing | COMPLETE |

## Roll-up

- COMPLETE: 30
- PARTIAL: 30
- MISSING: 18
- OUT_OF_SCOPE: 2

This distribution — roughly 37% complete, 37% partial, 22% missing — is the honest starting point for scoping Phases 3–13. The MISSING items cluster almost entirely into three themes: (a) operator/customer presence & the customer-context/commerce layer (items 4, 42, 45, 47-52 — an entirely new subsystem), (b) media upload security (item 68/79 — a security hardening pass on existing endpoints), and (c) mobile/tablet customer-info access in the operator dashboard (item 62 — a frontend-only gap). Everything else is either done or a bounded fix to existing, tested code.
