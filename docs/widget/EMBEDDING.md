# Embedding the RastiChat widget

The widget is one script (`widget.js`, built from `packages/widget`). It shows a small launcher (a message icon by
default) and opens a chat panel. **Everything about how it looks and behaves is configuration of the project** (see
`PRE_CHAT_CONFIGURATION.md`) — the embedding code stays the same.

## Minimal embed (guests)

```html
<script src="https://chat.example.com/widget.js"></script>
<script>
  RastiChat.init({
    projectKey: "<project public key>",              // public identifier — not a secret
    apiBase: "https://chat.example.com/api/v1",
    wsBase: "wss://chat.example.com/ws",
  });
</script>
```
`projectKey` is shown in the dashboard (Widget settings → Embed) and returned by tenant provisioning
(`project_public_key`). The browser's `Origin` must be one of the project's allowed domains.

## Signed-in customers: trusted bootstrap (no second login)

```html
<script>
  RastiChat.init({
    projectKey: "…", apiBase: "…", wsBase: "…",
    bootstrap: async () => {
      // YOUR backend decides who this customer is and signs a short-lived assertion (Contract v1 §7).
      const r = await fetch("/chat/identity", { credentials: "include" });
      return r.ok ? await r.text() : null;          // null = not signed in → guest (if the project allows guests)
    },
    context: { page: location.pathname },            // optional: values for the project's hidden pre-chat fields
  });
</script>
```
* The widget never trusts anything the page says about identity: it relays the assertion; RastiChat verifies the
  signature, audience, expiry, single use, tenant and origin.
* A stored session is reused only for **the same person** (the assertion's `sub` is compared to what the session
  was created for). A different signed-in person, or a signed-out page, never inherits the previous customer's session
  (the old one is revoked).
* When a guest signs in, their guest conversation is offered for attachment (header `X-Widget-Session`); the server
  attaches it only because the assertion proves who they are.
* Session renewal: when the server reports the session expired/revoked, the widget asks `bootstrap` for a fresh
  assertion once and carries on. `bootstrap` is called at most once per page load plus once per renewal.
* **Never** put an assertion, user id or role in markup, query strings or `localStorage` yourself.

## What the project configures (remote)
Launcher (icon / icon+text, label, tooltip, icon, colour, position, offsets, greeting bubble, auto-open, mobile
full-screen), start behaviour, pre-chat form, identity policy (guests allowed / signed-in only), locale and direction,
capability flags (attachments, voice, emoji), show/hide path rules. Delivered by `GET /api/v1/widget/config/`.
If that endpoint is unreachable or the server is older, the widget falls back to its original behaviour.

`init()` options `position` (`left`|`right`) and `primaryColor` override the remote values when given.

## Start behaviour — no empty conversations
| `behavior.start_mode` | when the conversation is created |
|---|---|
| `on_first_message` (default for provisioned tenants) | when the visitor sends the first message (queued in order, delivered once the socket is authenticated) |
| `on_open` | when the panel is opened |
| `on_load` (default for projects without configuration; original behaviour) | when the page loads |

A returning visitor with a stored session only *looks* for an existing open conversation (`create: false`), so history is
restored without creating anything. Pre-chat enabled ⇒ at least `on_first_message` (a question can only be asked before
the conversation exists).

## JavaScript API
`RastiChat.init(options)` · `RastiChat.open()` / `close()` · `RastiChat.logout()` (revokes the session — call it when the
customer logs out of your app) · `RastiChat.refreshVisibility()` (re-evaluate show/hide rules after a client-side
navigation; also automatic on `popstate`) · `RastiChat.destroy()`.

## Behaviour & quality
* **RTL/LTR** and Persian/English UI strings from the project's `locale`/`direction`.
* **Mobile**: full-screen panel (or a bottom sheet when `mobile.fullscreen` is off); safe-area insets; 16 px input font
  (no iOS zoom); body scroll locked while open.
* **Accessibility**: launcher is a focusable button (`aria-label`, `aria-expanded`, Enter/Space), panel is a labelled
  dialog closed with Escape, form fields have labels, required/invalid state and `role="alert"` messages, visible focus.
* **Offline/unavailable**: a banner while the socket reconnects (fresh single-use ticket each time; history is re-fetched
  so nothing is lost); if no session can be opened the launcher is greyed and a message explains (or asks the customer to
  sign in on signed-in-only projects).
* **Security**: no credential in any URL (session in the `X-Widget-Session` header, WebSocket by single-use ticket in the
  first frame); configuration text is rendered with `textContent` only (never HTML); the widget does not log tokens.

## Content-Security-Policy on the host page
`script-src https://chat.example.com`, `connect-src https://chat.example.com wss://chat.example.com`,
`style-src 'unsafe-inline'` (the widget injects one `<style>`), `font-src https://fonts.gstatic.com` +
`style-src https://fonts.googleapis.com` if you keep the (best-effort) Vazirmatn web font, `img-src` for your logo/avatars.
No `frame-src` is needed (the widget is not an iframe).

## Headless integration
Build your own UI against the same REST + WebSocket protocol (OpenAPI at `/api/schema/`): `POST /identity/customer/` (or
`/widget/init/` for guests) → `X-Widget-Session` header → `POST /widget/start/` → `POST /widget/ws-ticket/` → WebSocket
`/ws/v2/widget/<conversation>/` with `{"type":"auth","ticket":…}` as the first frame. Re-fetch
`GET /widget/conversations/<id>/messages/` after every (re)connect. See `docs/runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md`.
