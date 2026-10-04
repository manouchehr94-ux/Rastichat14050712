# Commerce & Customer-Context Integration

## Decision: Option A — local, tenant-scoped records

The prototype's operator info panel shows customer phone/location/membership-date/order-count/total-spend/score and a recent-orders list. RastiChat is a generic multi-tenant support platform — not every tenant is an e-commerce store, and the repository does not (and per the task's explicit instructions, should not) own a full commerce/checkout system.

Three options were on the table:

- **Option A — Local synchronized catalog**: tenant-scoped local records, either operator-entered or synced in by a future job, with a documented extension point for a real adapter.
- **Option B — External commerce adapter/API**: define and call out to a real e-commerce backend's API live.
- **Option C — Hybrid**: local snapshot + live adapter for freshness.

**Option A was chosen** for this pass. It is the only option that ships something real without either (a) building a fake shopping cart to imitate the prototype, which the task explicitly forbids, or (b) inventing a fictitious external API contract with no real backend behind it, which would be untestable and dishonest to label "implemented."

## What was built

New `customer_context` Django app (`backend/customer_context/`):

- `CustomerProfile` — a `Visitor`-extension model (`location`, `score`), one row per (visitor, workspace).
- `CustomerOrder` — a local order snapshot (`product_name`, `product_image`, `price`, `status`, `ordered_at`), tenant-scoped by `workspace` + `visitor`.
- `Tag` / `ConversationTag` — workspace-owned tag catalog and per-conversation attachment.
- `Note` — discrete, authored, timestamped private operator notes per conversation.

Endpoint: `GET /api/v1/conversations/customer/<id>/customer-context/` (`customer_context/views.py::CustomerContextView`) returns the rolled-up profile for the conversation's visitor:

```json
{
  "visitor_id": "...", "name": "...", "email": "...", "phone": "...",
  "location": "...", "customer_since": "...",
  "order_count": 3, "total_spent": "2340000", "score": "4.9",
  "tags": [...], "recent_orders": [...]
}
```

`order_count` and `total_spent` are **always computed live** from `CustomerOrder` rows (a `Sum` aggregate), never denormalized/stored, so they cannot drift out of sync with the underlying order records.

## Graceful degradation for non-commerce tenants

A workspace with no `CustomerOrder`/`CustomerProfile` rows for a visitor gets `order_count: 0`, `total_spent: "0"`, `location: ""`, `score: null` — not an error. The operator dashboard's `CustomerInfoPanel` renders `—` for these fields rather than hiding the panel or crashing (see `apps/operator-dashboard/src/app/page.tsx` `CustomerInfoPanel`, and the regression test `test_customer_context_degrades_gracefully_without_commerce_data` in `customer_context/tests.py`).

## Extension point for a real integration (Option B/C, not built)

If/when a tenant needs live commerce data, the natural seam is:

1. Add a management command or scheduled job that upserts `CustomerOrder`/`CustomerProfile` rows from an external system (keeps Option A's local-read-path unchanged — the operator dashboard and `CustomerContextView` would not need to change at all).
2. Or, replace `CustomerContextView.get()`'s local queryset reads with a call to an external adapter interface (e.g. `class CommerceAdapter: def get_customer_context(visitor) -> dict`), with the local-records path becoming the default/no-op implementation.

Neither is implemented in this pass — implementing a full external adapter without a real backend to call would produce an untestable, unverifiable "integration" that couldn't honestly be marked COMPLETE.

## Add-to-cart

The prototype's "افزودن به سبد خرید" (add to cart) button is decorative in the source prototype itself — it has no click handler in the prototype's own JavaScript. No cart/checkout system exists in this repository, and building one was explicitly out of scope per the task instructions ("do not build a complete shopping cart unless the repository already owns commerce behavior"). Recommended treatment when this becomes a real requirement: the button should link out to `Product`'s (not-yet-added) canonical product URL on the tenant's own storefront, not attempt an in-widget checkout.
