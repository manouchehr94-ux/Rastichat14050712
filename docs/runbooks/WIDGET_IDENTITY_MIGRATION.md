# Widget visitor identity: `external_id` hardening and migration

## What changed (P0-4)
`POST /api/v1/widget/init/` used to resolve `(project, external_id)` to an existing `Visitor`. Both inputs are public
(`project_key` is embedded in every page), so any anonymous caller who knew or guessed an `external_id` received a
session for that customer's visitor and could read their conversation history. Now:

| Request | Behaviour |
|---|---|
| no `external_id` (anonymous / guest) | unchanged: a fresh `Visitor` per init |
| `external_id` supplied, unsigned | a **fresh** `Visitor`; the claim is stored as `metadata.unverified_external_id` only |
| `external_id` + `WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID=true` | legacy lookup (spoofable), warning logged per use |

`Visitor.external_id` is reserved for identities asserted by a trusted server (planned signed-assertion/SSO work).

## Who is affected
- **Guests:** not affected.
- **Returning customers with an existing conversation:** not affected. The official widget stores its `session_token` in
  `localStorage` and resumes the same visitor/conversation through `/widget/start/` with that token; nothing in that path
  changed. Existing `Visitor` rows (including those with `external_id`) and their sessions stay valid; no data is rewritten.
- **A customer who cleared their browser storage:** previously re-attached to their history only if the embedding site sent
  the same `external_id`; now they start a fresh conversation until signed identity is available. This is the deliberate
  trade-off — the old behaviour was indistinguishable from an attack.
- **Custom embedders that send `external_id` to the public endpoint:** they keep working (200 + working session) but no longer
  get cross-session resolution.

## Migration plan
1. Deploy with the default (flag off). Verify guests and returning-session flows (tests below cover them).
2. If a specific embedding site truly depends on the old lookup, enable `WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID=true` only for
   the time needed (in staging/production this additionally requires `WIDGET_UNVERIFIED_EXTERNAL_ID_ACK=accept-spoofable-customer-identity`,
   and the security deploy gate reports `visitors.W001` while it is on), watch the warning log (`widget init used legacy unverified external_id lookup`), and treat that
   deployment as spoofable meanwhile.
3. Move to signed identity assertions (RastiSi server issues a short-lived signed token; chat validates signature, audience,
   expiry and one-time use, then maps the store and customer). Then `Visitor.external_id` is set only from a verified claim,
   scoped per store (`<store_public_id>:<customer_id>`), and guest→customer merge is explicit and idempotent.
4. Remove the legacy flag after step 3.

## Tests
`backend/visitors/tests.py` (init semantics, spoof attempt end-to-end, returning-session compatibility, legacy flag).
