# Pre-chat configuration (launcher, questions, identity policy)

Stored per project as a validated, versioned document (`ProjectWidgetConfig`, schema in `backend/projects/widget_config.py`).
Edit it in the operator dashboard (**🧩 Widget settings**, Owner/Admin of the project's workspace) or via API; an
integration can seed it when it provisions a tenant (`defaults.widget`, applied once).

* `GET  /api/v1/widget/config/?project_key=<key>` — public, effective configuration (defaults + stored), Origin-checked.
* `GET|PUT /api/v1/projects/<id>/widget-config/` — workspace Owner/Admin of THAT project's workspace only (an admin role in
  another workspace never counts). `PUT` replaces the stored document after strict validation (unknown keys, bad
  types/sizes → `400 {"errors": {"pre_chat.fields[0].key": "…"}}`).
* `GET /api/v1/projects/` — projects of workspaces you administer.

## Document (version 1)
```json
{
  "version": 1,
  "launcher": {"enabled": true, "mode": "icon|icon_text", "position": "bottom-right|bottom-left", "offset": {"x": 20, "y": 20},
               "label": "", "tooltip": "", "icon": "chat|help|headset|mail|sparkle", "color": "#BC5A38",
               "greeting": "", "auto_open": false, "mobile": {"fullscreen": true}},
  "visibility": {"hide_on_paths": ["/checkout*"], "show_on_paths": []},
  "behavior": {"start_mode": "on_load|on_open|on_first_message"},
  "pre_chat": {"enabled": false, "title": "", "submit_label": "", "fields": []},
  "identity": {"guest_allowed": true, "authenticated_only": false},
  "locale": "fa|en", "direction": "rtl|ltr",
  "capabilities": {"attachments": true, "voice": true, "emoji": true, "rating": true}
}
```
Limits: label ≤ 40, tooltip ≤ 120, greeting ≤ 200 chars; colour `#RRGGBB`; offsets 0–200; no markup (`<`/`>`) in any text; paths
`/…` with `*` wildcards (≤ 20). Business-hours behaviour is **not** part of v1 (the SLA business calendars remain
operator-side); `launcher.enabled=false` hides the widget.

## The three questions modes — pure configuration
**A. No questions** — `"pre_chat": {"enabled": false}`: click the icon, the composer opens, the first message starts the
conversation.

**B. One question**
```json
{"pre_chat": {"enabled": true, "fields": [
  {"key": "help", "type": "text", "label": "What can we help you with?", "required": true}]}}
```
**C. Structured form**
```json
{"pre_chat": {"enabled": true, "title": "Before we start", "submit_label": "Start chat", "fields": [
  {"key": "topic", "type": "select", "label": "Topic", "required": true, "order": 1,
   "choices": [{"value": "order", "label": "My order"}, {"value": "billing", "label": "Payment"}]},
  {"key": "order_no", "type": "text", "label": "Order number", "order": 2, "max_length": 20},
  {"key": "language", "type": "radio", "label": "Preferred language", "order": 3,
   "choices": [{"value": "fa", "label": "فارسی"}, {"value": "en", "label": "English"}]},
  {"key": "details", "type": "textarea", "label": "Description", "order": 4},
  {"key": "agree", "type": "consent", "label": "I accept the terms", "required": true, "order": 5},
  {"key": "page", "type": "hidden", "label": "", "order": 6}]}}
```
Field: `key` (`^[a-z][a-z0-9_]{0,39}$`, unique), `type` (`text|textarea|email|phone|select|radio|checkbox|consent|hidden`),
`label` (≤120), `placeholder`, `required`, `order`, `choices` (select/radio only, 1–20), `max_length` (1–2000),
`enabled`. ≤ 12 fields. No free-form regular expressions (no ReDoS surface); `email`/`phone` have built-in validation.
`hidden` fields are never shown: their values come from the host page (`RastiChat.init({context: {page: …}})`) and are
stored flagged `source: "client"` — operators see them as **unverified**, automations must treat them like message content.

## Where the answers go
Validated **server-side** against the project's *current* form when the conversation is created (`POST /widget/start/`
with `pre_chat: {key: answer}`): required, type, length, allowed choices, unknown keys refused. Nothing is created if
anything is invalid (`400 pre_chat_invalid` with per-field messages; the widget shows them next to the fields).
Stored as `conversations.PreChatSubmission` (`answers: [{key, label, type, value, source}]`, labels snapshotted so editing
the form never rewrites history) — **not** as columns.
* **Operator**: *Customer panel → "پاسخ‌های پیش از گفتگو"* (`customer-context` API: `pre_chat`, `identity_verified`).
* **Routing / automations**: condition field `conversation.pre_chat` with `path = <question key>` (e.g. topic equals
  `billing` → set queue/team/priority) — evaluated on `CONVERSATION_CREATED` with the answers already saved.
* **Events/webhooks**: available to the event payload when webhook delivery lands (Contract §13).
* Resuming a conversation never asks again and never overwrites stored answers.

## Identity policy
`guest_allowed: false` / `authenticated_only: true` is **enforced server-side**: `POST /widget/init/` refuses
(`403 identity_required`) and `POST /widget/start/` refuses guest sessions; only a customer with a host-signed assertion
(`/identity/customer/`) can chat. Tightening the policy also stops existing guest sessions from starting new conversations.

## Compatibility
Projects without a stored configuration behave exactly as before (`on_load`, defaults above). The widget falls back to its
original behaviour if the configuration endpoint is unreachable. `POST /widget/start/` is unchanged unless the new
optional fields `create` / `pre_chat` are sent.
