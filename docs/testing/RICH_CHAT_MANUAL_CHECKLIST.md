# Rich Chat — Manual QA Checklist

For a human reviewer verifying this pass in a running environment (see `docs/runbooks/RICH_CHAT_LOCAL_RUN.md` to stand one up). Each item cites the automated coverage that already exists, so this checklist is for spot-checking things that are hard to assert automatically (visual polish, RTL feel, animation), not re-deriving what the test suites already prove.

## Customer widget

- [ ] Widget renders RTL, Persian text reads right-to-left, Vazirmatn font loads (falls back to Tahoma if blocked — try with the font request blocked in devtools). *(automated: `packages/widget/src/main.test.ts`)*
- [ ] Send a text message; it appears immediately (optimistic) and again is not duplicated when the WS echo arrives. *(automated: dedup test)*
- [ ] Emoji picker opens, inserting an emoji appends it to the input without sending. *(automated)*
- [ ] Quick-reply chip fills the input, doesn't auto-send. *(automated)*
- [ ] Attach a real image (jpg/png/webp/gif); attach a renamed non-image file and confirm it's rejected with a clear error, not a silent failure. *(automated: `tests_media_security.py`)*
- [ ] Record a voice note (needs a real mic or a browser flag to fake one); confirm playback, duration, and waveform-style bar render.
- [ ] Operator shares a product; confirm brand/name/current price/old price (with strikethrough)/rating/review count all render correctly and match the shared product. *(automated: E2E)*
- [ ] Operator requests a rating; star picker appears, clicking a star submits and shows the thank-you state; submitting a second rating on the same conversation is rejected (not silently overwritten). *(automated: `tests_media_security.py::RatingResubmissionTests`)*
- [ ] Refresh the page mid-conversation; full history reloads, no duplicates. *(automated: E2E)*
- [ ] Resize to a mobile width; composer, bubbles, and popovers stay usable (no horizontal scroll, popovers don't overflow the viewport). *(automated: E2E mobile-widget smoke test — visual polish not covered, spot-check manually)*
- [ ] Kill and restart the backend mid-session; widget shows no fatal error, reconnects within ~2s, no duplicate messages on reconnect. *(automated: reconnect test, via socket close rather than an actual backend restart)*

## Operator dashboard

- [ ] Conversation list shows visitor name, last-message preview, timestamp, unread badge, status chip. *(automated: `page.test.tsx`)*
- [ ] Search box filters by visitor name/subject/last-message content. *(automated)*
- [ ] Status tabs (همه/باز/در انتظار/بسته) filter the list. *(automated)*
- [ ] Selecting a conversation loads history, connects a live socket, marks it read, and loads tags/notes/customer-context. *(automated)*
- [ ] Product picker lists real workspace products (not sample data) and shares correctly. *(automated: E2E)*
- [ ] Image upload from the operator side works and is subject to the same validation as the widget side. *(automated: backend tests)*
- [ ] Voice message send from the operator side (requires a mic) — **not covered by any automated test in this pass**; the prototype itself doesn't give the operator composer a mic button either, so this is intentionally out of scope, confirm this is still the desired product behavior.
- [ ] Tags: click a workspace tag chip to attach/detach it on the selected conversation; confirm it's scoped to the workspace (a tag created in one workspace must not be attachable from another). *(automated: `customer_context/tests.py`)*
- [ ] Notes: add a note, confirm author + timestamp render, confirm the note list is never reachable from any visitor-facing surface. *(automated)*
- [ ] Customer-info panel: verify phone/location/order-count/total-spend/recent-orders render for a workspace with seeded `CustomerOrder` data, and gracefully show `—` (not an error) for a workspace with none. *(automated)*
- [ ] Resize below the `lg` breakpoint: the desktop side panel disappears; clicking the ⓘ button in the chat header opens a full-screen info overlay with a working ✕ back control. *(automated: `page.test.tsx` mobile-nav test)*
- [ ] Resize below the `md` breakpoint: selecting a conversation shows the chat pane and hides the list; the `›` back button returns to the list. *(automated)*

## Cross-tenant / security spot checks

- [ ] Log in as an operator in one workspace; confirm the product picker, tag list, and conversation list never show another workspace's data. *(automated: extensive backend tests + `customer_context/tests.py`)*
- [ ] Open the widget in two separate private-browsing windows (or two Playwright-style isolated contexts); confirm neither sees the other's messages. *(automated: E2E "customer A cannot see customer B" — this is exactly the scenario where a real bug was caught and fixed this pass; see the final report)*
- [ ] Attempt to fetch another conversation's message history using a valid session token for a *different* conversation; confirm 404. *(automated: backend tests)*
