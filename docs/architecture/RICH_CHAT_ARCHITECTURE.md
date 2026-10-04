# Rich Chat Architecture

This document describes how rich messages (text/image/voice/product/rating), realtime delivery, and the customer-context layer actually work in the current implementation.

## Components

- **Backend**: Django + Django REST Framework + Channels (ASGI, served by Daphne), PostgreSQL, Redis (as the Channels layer backend).
- **Widget** (`packages/widget`): a dependency-free TypeScript IIFE bundle, embeddable via a single `<script>` tag. No shared runtime with the operator dashboard.
- **Operator dashboard** (`apps/operator-dashboard`): Next.js/React, talks to the same REST/WS API as the widget but authenticates as a JWT-bearing operator instead of a session-token-bearing visitor.
- **Platform dashboard** (`apps/platform-dashboard`): a separate support-ticket surface for workspace↔platform escalation; not part of the rich customer-chat surface.

## Identity and authentication

| Actor | Identity mechanism | Enforcement point |
|---|---|---|
| Visitor (customer) | `VisitorSession.token` (random UUID), resolved server-side to a `Visitor` row | Every widget REST call and the `/ws/widget/<token>/<conv_id>/` WS route |
| Operator | JWT (SimpleJWT access token) | `DEFAULT_AUTHENTICATION_CLASSES` for REST; manually decoded via `AccessToken(...)` in `DashboardChatConsumer.connect()` for WS, since Channels doesn't get header-based DRF auth for free |

**Sender identity is never trusted from the client.** Every message-creation path (`views.py` REST endpoints and both `consumers.py` WS handlers) sets `sender`/`sender_visitor` from the server-resolved identity, ignoring any sender field in the request body. Regression-tested in `conversations/tests_baseline.py` (`test_12`–`test_14`) and `tests_support.py` (`test_12`).

As of this pass, **anonymous visitor identity is also never collapsed across sessions**: `InitVisitorView` (`backend/visitors/views.py`) creates a fresh `Visitor` per anonymous init call. A caller-supplied `external_id` is the only way two sessions resolve to the same `Visitor` (a genuinely known/returning customer). See `docs/audit/PROTOTYPE_ALIGNMENT_FINAL_REPORT.md` for how this was found (a real bug caught by the new Playwright E2E suite, not by the pre-existing unit tests).

## Data model

- `Conversation` (`conversations/models.py`): `type` (CUSTOMER / PLATFORM_SUPPORT), `status`, `workspace`, `visitor`, `assigned_to`, `rating`, legacy flat `notes` field (superseded — see below).
- `Message`: `message_type` (TEXT/IMAGE/VOICE/PRODUCT/RATING_REQUEST/RATING), `metadata` (JSON — holds caption/duration/product snapshot/rating value), `attachment` (FileField), `client_message_id` (unique per conversation — the idempotency key), `sender_type` + `sender`/`sender_visitor`.
- `MessageReceipt`: one row per (message, reader) — the source of truth for "seen" state, derived per-message in `MessageSerializer.get_seen`.
- `customer_context` app (new this pass): `Tag` / `ConversationTag` (workspace-owned tag catalog + per-conversation attachment), `Note` (discrete, authored, timestamped operator notes — replaces the flat `Conversation.notes` field going forward), `CustomerProfile` / `CustomerOrder` (local, tenant-scoped CRM-style extension of `Visitor` — see `docs/architecture/COMMERCE_INTEGRATION.md` for why this is local-only).

## Realtime events

All delivered over a per-conversation Channels group (`chat_{conv_id}` for customer conversations, `support_chat_{conv_id}` for platform-support conversations):

| Event | Trigger | Payload shape |
|---|---|---|
| `chat.message` | Any message create (REST or WS) | The full serialized `Message` (JSON-round-tripped before `group_send` — the channel layer serializes with msgpack, which can't handle a raw UUID from a DRF `PrimaryKeyRelatedField`) |
| `typing.indicator` | Client sends `{type: 'typing'}` over WS | `{type: 'typing', sender_type}`, with self-echo suppressed via `origin_channel` |
| `message.seen` | `mark_read` REST action or WS `{type: 'mark_read'}` | `{type: 'message.seen', reader: 'USER'|'VISITOR'}` |

The support-channel consumer (`DashboardSupportConsumer`) does not implement typing/mark-read — only plain message send. This is a known scope difference, not a bug: the platform-support surface never adopted the rich-message feature set.

## Idempotency, ordering, reconnect

- Every message-creation path requires a client-supplied `client_message_id`; `Message.Meta.unique_together = ('conversation', 'client_message_id')` enforces it at the database level, not just in application code. A duplicate returns HTTP 409 (REST) or is silently dropped (WS).
- Both the widget (`main.ts`, `renderedIds` Set) and the operator dashboard (`page.tsx`, `renderedIds` ref) deduplicate client-side on `client_message_id` before rendering, since a message can legitimately arrive twice: once as the optimistic/REST-response render, once as the live WS echo.
- Both clients reconnect on WS close: the widget with a flat 2s retry (`main.ts`), the dashboard by opening a fresh socket whenever the selected conversation changes.
- Message ordering is `created_at` ascending (`Message.Meta.ordering`); this is a straightforward `ORDER BY`, not a vector-clock or sequence-number scheme — acceptable given this codebase's scale, called out here so a future change to true multi-writer ordering doesn't get treated as a regression fix.

## Tenant isolation

Enforced at the queryset level throughout `conversations/views.py`, `catalog/views.py`, and the new `customer_context/views.py`, all filtering on `workspace__memberships__user=request.user` (or the platform-support equivalent), and re-verified at the WS layer in each consumer's `_get_*_conversation` helper. Extensively regression-tested — see the audit doc for the full list of dedicated cross-tenant-denial tests.

## Known architectural limitations (carried forward, not fixed this pass)

- Message ordering has no tie-breaker beyond `created_at`; two messages created within the same DB timestamp resolution could theoretically interleave. Not observed in practice at this scale.
- The support-channel consumer's lack of typing/read-receipt support is a deliberate scope boundary, not tracked as a bug.
- `VisitorSession.expires_at` exists in the schema but is never enforced — sessions are effectively permanent. Flagged in the audit as a MISSING item, not fixed this pass (a real fix requires deciding a session lifetime policy, which is a product decision, not a mechanical one).
