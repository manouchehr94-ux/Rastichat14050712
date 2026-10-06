# Integrate RastiChat into a new application

A walkthrough for a developer who has **only** this documentation. The working reference is `examples/reference-host/`
(a ~150-line Node app with its own users and organisations — not RastiSi), proven by automated tests
(`backend/integrations/tests_e2e.py`, `e2e/reference-host/`). Normative details: `INTEGRATION_CONTRACT_V1.md`.

You need: a RastiChat deployment you (or its operator) control, and a backend of your own that can sign a JWT with Ed25519
(any language; Node has it built in, Python `PyJWT[crypto]`, Go/Java/PHP/Ruby/.NET libraries all do).

## 1. Create / register the integration
1. **You** generate an Ed25519 keypair and keep the private key in your secret store:
   `openssl genpkey -algorithm ed25519 -out host.private.pem && openssl pkey -in host.private.pem -pubout -out host.public.pem`
2. The **RastiChat operator** registers you and your *public* key:
   ```
   manage.py integration_create --slug my-app --name "My App" --platform-external-id <platform> \
       --scopes tenants:read,tenants:write,identity:customer,identity:staff
   manage.py integration_key_add --integration my-app --public-key-file host.public.pem     # prints kid=ick_…
   ```
   Least privilege: give only the scopes you need (a key can be narrowed further with `--scopes`).

## 2. Provision a tenant/project (idempotent)
For each of *your* customers' organisations (a "tenant"): `PUT /api/v1/integrations/tenants/<your-org-id>/`
with `{"display_name", "verified_domains", "defaults": {...}}`. Sign every request (Contract §3.2); a 20-line helper is in
`examples/reference-host/lib/rastichat.mjs`. The response contains `project_public_key` — store it next to your org.
Repeat calls never create duplicates and never overwrite what a RastiChat admin configured by hand (Contract §6).

## 3. Allowed domains
`verified_domains` are exact hostnames you have verified (no wildcards). They become the project's allowed domains: only
pages on those origins can create sessions. (RastiChat admins may add more by hand; you can never remove those.)

## 4. Launcher and pre-chat
Seed once with `defaults.widget`, or let the tenant's admin use **Widget settings** in the dashboard. Examples:
* icon only, no questions: `{"launcher": {"mode": "icon"}}`
* one question: `{"pre_chat": {"enabled": true, "fields": [{"key":"help","type":"text","label":"What can we help you with?","required":true}]}}`
* structured form: see `docs/widget/PRE_CHAT_CONFIGURATION.md`.
No code changes in RastiChat or in your page — it is configuration.

## 5. Your server-side identity endpoint
```js
// GET /chat/identity — YOUR backend, YOUR session. Never read tenant/user/role from the request.
const user = currentUser(req);                       // from your own login session
if (!user) return res.status(401).end();
res.type('text/plain').send(rastichat.assertion({ actor: 'customer', sub: user.id, tenant: user.orgId, name: user.name,
                                                  origin: 'https://' + req.headers.host }));
```
Assertions live ≤ 2 minutes (use 60 s) and are single-use. Staff: `actor: 'tenant_staff'` + generic `role`
(`owner|admin|operator`), then send the browser to `<dashboard>/sso#assertion=<jwt>&next=/` (fragment, not query string).
Optionally pre-sync staff with `PUT /integrations/tenants/<t>/members/<user>/`.

## 6. Embed the widget
```html
<script src="https://chat.example.com/widget.js"></script>
<script>RastiChat.init({ projectKey: "<project_public_key>", apiBase, wsBase,
  bootstrap: async () => { const r = await fetch('/chat/identity'); return r.ok ? r.text() : null; } })</script>
```
Guests work when the project allows them (`bootstrap` returns `null`). More: `docs/widget/EMBEDDING.md`.

## 7. Headless (your own UI)
Same REST + WebSocket protocol, OpenAPI at `/api/schema/` — see the headless section of `docs/widget/EMBEDDING.md`.

## 8. Events
Webhook/event delivery is specified (Contract §13) and not yet implemented; poll the REST API or use the operator
dashboard until it ships.

## 9. Revoke / deprovision
| You want to… | Call |
|---|---|
| remove a staff member from a tenant | `DELETE /integrations/tenants/<t>/members/<user>/` |
| disable a person everywhere | `POST /integrations/users/<user>/disable/` |
| cut off a customer | `POST /integrations/tenants/<t>/customers/<user>/disable/` |
| suspend / archive a tenant | `PUT … {"status":"suspended"}` / `DELETE /integrations/tenants/<t>/` |
| rotate your key | add the new public key, switch, revoke the old (`integration_key_revoke`) |
History is always retained. Run `GET /api/v1/integrations/me/` to verify connectivity and see your scopes.

## Checklist
- [ ] private key in a secret store, never in a browser/repo/log
- [ ] fresh token per request, TTL ≤ 60 s; assertion ≤ 120 s
- [ ] tenant/user/role derived server-side only
- [ ] `verified_domains` registered; CSP allows the RastiChat origin
- [ ] logout → `RastiChat.logout()`; user removal → deprovisioning calls
- [ ] chat treated as optional UI (launcher hidden if RastiChat is down)
