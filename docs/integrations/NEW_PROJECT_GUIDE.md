# Integrating RastiChat into a New Project

A practical, step-by-step guide for a developer who has **only this documentation**. When you are done, your product has: a small chat
icon on your pages; customers who are already signed in to *your* app chatting without a second login (guests too, if you allow them);
your staff answering from the RastiChat inbox through single sign-on; and a way to cut off access the moment someone leaves.

* Normative reference: [`INTEGRATION_CONTRACT_V1.md`](INTEGRATION_CONTRACT_V1.md) (every field, code and rule).
* A complete working example: [`examples/reference-host/`](../../examples/reference-host/) — "Acme Learn", a ~150-line Node app with its own
  users and organisations, which has nothing to do with any other product. It runs in CI on every build.
* Don't want the widget? Build your own UI: [`HEADLESS.md`](HEADLESS.md).
* What exists today and what is deliberately deferred: [`V1_SCOPE_AND_DEFERRALS.md`](V1_SCOPE_AND_DEFERRALS.md).

All example values (`my-app`, `shop-1`, `chat.example.com`, `ick_…`) are placeholders.

## How the pieces fit

```
 your backend (holds a private key) ──signs──► JWTs ──► RastiChat API   (provision tenants, members, context)
 your backend ──signs "this browser is customer 42 of tenant shop-1"──► your page ──relays──► RastiChat ──► chat session
 your page: <script widget.js> RastiChat.init({projectKey, bootstrap})   (or your own UI, headless)
 your staff:  your app redirects to  <operator dashboard>/admin/sso#assertion=…   → inbox, no password
```

Vocabulary: your **tenant** is one customer organisation of yours (a shop, an account, a school…); your **platform** is your own team.
RastiChat maps each tenant to a *workspace* with one *project* (the widget deployment). You need: a RastiChat deployment (yours or one an operator
runs for you) and a backend that can sign an Ed25519 JWT (Node has it built in; Python `PyJWT[crypto]`; Go, Java, PHP, Ruby, .NET all do).

## 1. Register (create) the integration

An integration is your application's identity inside RastiChat. The **RastiChat operator** registers it:

```bash
python manage.py integration_create --slug my-app --name "My App" --platform-external-id <your platform's id> \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,context:write
```

Grant only the scopes you need: `tenants:*` (provisioning), `identity:customer`, `identity:staff`, `identity:platform`, `context:write`,
`conversations:initiate`. Check them later with a signed `GET /api/v1/integrations/me/`.

## 2. Generate and register your signing key

**You** generate the keypair and keep the private key in your secret store. RastiChat only ever receives the **public** half.

```bash
openssl genpkey -algorithm ed25519 -out host.private.pem
openssl pkey -in host.private.pem -pubout -out host.public.pem
```

Send `host.public.pem` to the operator, who runs:

```bash
python manage.py integration_key_add --integration my-app --public-key-file host.public.pem   # prints kid=ick_…
```

`kid` goes into every JWT header. (`integration_keygen` exists only for demos and tests; never generate production keys on the RastiChat server.)

### The signer (the only "SDK" you need)

Server-to-server calls carry a **new token per request** (also per retry) bound to the method, the path and the body:

```python
# Python (PyJWT[crypto])
import base64, hashlib, json, time, uuid, jwt, requests
def call(method, path, body=None, *, base, slug, kid, private_pem):
    raw = b"" if body is None else json.dumps(body).encode()
    now = int(time.time())
    token = jwt.encode({"iss": slug, "sub": slug, "aud": "rastichat:api", "iat": now, "exp": now + 30, "jti": uuid.uuid4().hex,
                        "htm": method, "htu": path,                       # path exactly as sent: no scheme, host or query
                        "bh": base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()},
                       private_pem, algorithm="EdDSA", headers={"kid": kid})
    return requests.request(method, base + path, data=raw or None,
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
```

A zero-dependency Node version is `examples/reference-host/lib/rastichat.mjs`. Use `aud = "<audience>:api"`; the default audience is `rastichat`
(`INTEGRATION_TOKEN_AUDIENCE` on the deployment — ask the operator).

## 3. Provision a tenant (idempotent)

For each customer organisation of yours (`call(...)` below is the helper from step 2 with your base URL and credentials bound):

```bash
call("PUT", "/api/v1/integrations/tenants/shop-1/", {
  "display_name": "Acme Shop", "verified_domains": ["shop.acme.com"],
  "defaults": {"widget": {"launcher": {"mode": "icon"}}}})        # seed-only; see steps 6–7
```

→ `{"project_public_key": "<uuid>", "workspace_id": 12, …}`. **Store `project_public_key` next to your organisation record** — it is the public
identifier the widget needs. Repeating the call never duplicates anything and never overwrites what a RastiChat admin configured by hand.
Suspend with `{"status":"suspended"}`, archive with `DELETE`, restore with `{"status":"active"}`.

## 4. Provision and map your staff

You decide who answers for which tenant and map **your** roles to the three generic ones — `owner`, `admin`, `operator`:

```bash
call("PUT", "/api/v1/integrations/tenants/shop-1/members/u42/", {"role": "operator", "display_name": "Sam"})
```

Pre-provisioning is optional (staff are also created on their first SSO). Use **the same ids** in every later call (`members/{user}`,
`users/{user}/disable/`). If one person staffs several tenants and must not see across them, use a *different* `sub` per tenant
(e.g. `u42.shop-1`) — Contract §7.

## 5. Configure allowed domains

`verified_domains` are the **exact hostnames** (add `:port` for non-default ports; no wildcards from the host side) of the pages that embed the
chat. Only those origins can create sessions or open sockets for this project. A wrong or missing domain is the number-one cause of
"the icon does not appear" ([`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md)).

## 6. Configure the launcher

The icon is configuration, not code. Seed it with `defaults.widget` at provisioning (applied once), or let the tenant's admin use
*Widget settings* in the operator dashboard.

```json
{"launcher": {"mode": "icon"}}                                                      // small icon only (default look)
{"launcher": {"mode": "icon_text", "label": "Ask us", "position": "bottom-left"}}   // icon + label
```

Position, offsets, colour, greeting, auto-open, mobile full-screen, RTL/LTR, locale and show/hide path rules are in
[`../widget/EMBEDDING.md`](../widget/EMBEDDING.md).

## 7. Configure pre-chat questions (or none)

Pure configuration, same schema everywhere ([`../widget/PRE_CHAT_CONFIGURATION.md`](../widget/PRE_CHAT_CONFIGURATION.md)):

```json
{"pre_chat": {"enabled": false}}                                                    // click the icon → write → done
{"pre_chat": {"enabled": true, "fields": [{"key":"help","type":"text","label":"What can we help you with?","required":true}]}}   // one question
{"pre_chat": {"enabled": true, "title": "Before we start", "fields": [ … select / radio / textarea / consent / hidden … ]}}      // structured form
```

Answers are validated server-side, stored with the conversation and shown to the operator.

## 8. Build the host-side identity endpoint

This is what lets signed-in customers skip a second login. An endpoint of **your** backend, using **your** session, signs a short-lived
assertion. Never read tenant, user or role from the request:

```js
// GET /chat/identity   (your backend)
const user = currentUser(req);                     // from YOUR login session
if (!user) return res.status(401).end();           // not signed in → the widget falls back to guest (if allowed)
res.type('text/plain').send(rastichat.assertion({
  actor: 'customer', sub: user.id, tenant: user.orgId, name: user.displayName, origin: 'https://' + req.headers.host }));
```

The assertion is a JWT with `aud = <audience>:identity`, `actor`, `sub`, `tenant`, optional `name`/`origin`, lifetime ≤ 120 s (use 60), single
use. Sign a fresh one for every request — never cache it. Optionally push a small context snapshot for your operators
(`PUT …/contexts/{customer}/ {"profile": {"plan": "pro"}, "context": {"page": "/cart"}}`, flat, ≤ 4 KB, no credentials).

## 9. Embed the widget

```html
<script src="https://chat.example.com/widget.js"></script>
<script>
  RastiChat.init({
    projectKey: "<project_public_key>",
    apiBase: "https://chat.example.com/api/v1", wsBase: "wss://chat.example.com/ws",
    bootstrap: async () => { const r = await fetch('/chat/identity', {credentials: 'include'}); return r.ok ? r.text() : null; },
  });
</script>
```

When your user logs out call `RastiChat.logout()`. Add the RastiChat origin to your page's CSP (`script-src`, `connect-src` incl. `wss:`).

## 10. Test authenticated users

Sign in to your app, open a page with the icon, send a message. Expected: no login prompt; the conversation appears in the tenant's inbox
with a **verified** badge; reload the page and sign in again — the **same** conversation resumes.
Staff: have your app redirect the signed-in staff member to `https://operator.example.com/admin/sso#assertion=<staff assertion>&next=/`
(`actor: "tenant_staff"`, `tenant`, `role`). Always use the URL **fragment**, never a query string.

## 11. Test guests

Open the page signed out (or `bootstrap` returning `null`). If the tenant allows guests (`identity.guest_allowed`, default) a guest session is
created and chatting works; on `authenticated_only` tenants the icon invites the visitor to sign in. When a guest signs in, their guest conversation is
attached to their verified identity only because the assertion proves who they are.

## 12. Test revocation

| Do | Expect |
|---|---|
| `DELETE …/tenants/shop-1/members/u42/` | that person's inbox access stops (REST immediately, an open socket closes `4403` shortly after) |
| `POST …/tenants/shop-1/customers/c9/disable/` | that customer's sessions are revoked and new exchanges refused |
| `PUT …/tenants/shop-1/ {"status":"suspended"}` | widget stops for the tenant, staff lose access, history is kept |
| revoke the old key after a rotation | requests signed with it fail with `key_revoked` |

## 13. Optional: headless mode (your own chat UI)

Everything the widget does is public API: `POST /identity/customer/` → `X-Widget-Session` → `POST /widget/start/` → `POST /widget/ws-ticket/` → WebSocket
`/ws/v2/widget/<conversation>/` → `GET …/messages/` after each reconnect. [`HEADLESS.md`](HEADLESS.md) explains each step and
[`../WEBSOCKET_PROTOCOL.md`](../WEBSOCKET_PROTOCOL.md) the socket; `examples/headless/headless.mjs` runs the whole thing.

## 14. Monitor integration health

* Connectivity: signed `GET /api/v1/integrations/me/` (shows effective scopes) — put it in your own health check.
* Treat chat as **optional UI**: if RastiChat is unreachable or an assertion fails, hide the launcher or fall back to guest; no function of your
  product may depend on chat.
* The operator watches `events_last_24h` on the monitoring endpoint: `token_refused:*` (replay/invalid/expired → your signer or clock),
  `scope_denied`, `cross_tenant_denied:*`, `rate_limited` ([`../OPERATIONS.md`](../OPERATIONS.md)).
* `429` → back off with `Retry-After`; `503 replay_store_unavailable` → retry later.

## 15. Rotate and revoke keys

1. Generate a new keypair; give the operator the new **public** key → new `kid` (`integration_key_add`).
2. Deploy your backend with the new private key and `kid`; both keys verify during the overlap.
3. Operator revokes the old one (`integration_key_revoke --kid <old> --reason rotated`) — effective on the next request.
Rotate at least yearly and on any suspected exposure. If a key leaks: revoke it immediately; if unsure of the extent the operator can disable the whole
integration (`integration_set_active --slug my-app --disable`) — tenants and history are untouched.

## Checklist

- [ ] private key only in a secret store; never in a browser, repo or log
- [ ] fresh token per request (TTL ≤ 60 s) and fresh assertion per page load (≤ 120 s)
- [ ] tenant, user and role derived **server-side** from your own session
- [ ] `verified_domains` registered; CSP allows the RastiChat origin
- [ ] logout → `RastiChat.logout()`; user/membership removal wired to the deprovisioning calls (Contract §8)
- [ ] chat treated as optional UI
- [ ] webhooks are **not** part of v1 — don't plan on being called back ([`V1_SCOPE_AND_DEFERRALS.md`](V1_SCOPE_AND_DEFERRALS.md))
