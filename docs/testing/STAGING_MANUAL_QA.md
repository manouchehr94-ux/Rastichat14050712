# Staging Manual QA Checklist

Cross-references the 20 Phase 6 staging smoke scenarios. Scenarios 1-18
are automated in `e2e/staging-smoke/smoke.spec.ts` (run it first — this
checklist exists for the 2 scenarios a browser can't automate, and as a
manual fallback/double-check for the rest).

## Automated (`cd e2e/staging-smoke && npx playwright test`)

1. HTTPS Widget loads.
2. Widget JS initializes.
3. Customer starts a conversation.
4. Customer sends a message.
5. Operator logs in.
6. Operator sees the conversation.
7. Operator replies.
8. Customer receives the reply over WSS.
9. Refresh preserves history.
10. Image upload works.
11. Voice upload works (fake media device — real browser mic behavior
    should still be spot-checked manually once per release, see below).
12. Article card works (KB).
13. Macro execution works.
14. Automation executes.
15. Internal note stays private.
16. Cross-workspace/cross-project access rejected.
17. Platform Dashboard loads.
18. Health endpoints report ready (including that `/health/monitoring/`
    correctly rejects an anonymous caller with 401 and, if
    `SMOKE_MONITORING_TOKEN` is set, accepts one presenting it).

**Result:** PASS / FAIL — attach the Playwright HTML report
(`e2e/staging-smoke/smoke-report/`) from the run.

## Manual — scenario 19: Redis restart / reconnect

1. Open the Widget on the storefront (or `embed.html`) and start a
   conversation. Keep the operator dashboard open on the same
   conversation in another tab.
2. On the VPS: `docker compose -f docker-compose.staging.yml --env-file .env.staging restart redis`.
3. Send a message from the customer side within a few seconds of the restart.
4. Confirm: the message either fails visibly (retry-able) or is
   delivered once the WebSocket reconnects — never silently lost, never
   duplicated.
5. Confirm `curl https://chat-staging.rastisi.ir/api/v1/health/ready/`
   reports `redis.up: false` during the outage and `true` again after.
6. Confirm a second, unrelated workspace's conversation (if you have
   one) shows no cross-workspace message leakage after reconnect.

**Result:** PASS / FAIL

## Manual — scenario 20: container restart persistence

1. Send a message and upload an image in a test conversation.
2. Note the conversation ID and the image's `/media/...` URL.
3. On the VPS: `docker compose -f docker-compose.staging.yml --env-file .env.staging restart backend`.
4. Once healthy again (`curl .../health/ready/` returns 200), reload the
   conversation.
5. Confirm: the message and the image are both still present and the
   image URL still loads (proves Postgres/media survived on their
   volumes, not container-local storage).

**Result:** PASS / FAIL

## Manual — real browser microphone (once per release)

The automated voice-message scenario (11) uses Playwright's fake media
device flag, which exercises the app's handling of a voice recording
but not a real browser's actual microphone permission prompt/hardware
path. Once per release, manually:

1. Open the Widget in a real Chrome/Firefox/Safari on a real device.
2. Record and send a voice message; confirm the operator can play it back.

**Result:** PASS / FAIL

## Manual — real storefront embed (once per release, or whenever the embed snippet changes)

1. Embed the real `<script src="https://chat-staging.rastisi.ir/widget.js">`
   snippet (see `docs/runbooks/STAGING_DEPLOYMENT.md` section 8) on an
   actual page under the Rastisi storefront domain listed in
   `CORS_ALLOWED_ORIGINS` — not `embed.html`.
2. Confirm the widget loads and a conversation round-trips end to end.

**Result:** PASS / FAIL
