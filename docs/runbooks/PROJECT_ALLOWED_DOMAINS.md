# Project allowed domains (P1-4)

`Project.allowed_domains` used to be a decorative text field. It is now validated, enforced and wired into CORS / the
WebSocket handshake. (`projects/domains.py`, `visitors/sessions.py`, `projects/cors.py`, `projects/ws_origin.py`.)

## Syntax
Comma / space / newline separated hosts. Normalised on save (lower-case, scheme and trailing `/` stripped):

| entry | matches |
|---|---|
| `shop.example.com` | that host on the default port (`https://shop.example.com`, `http://…`) — not `:8443` |
| `shop.example.com:8443`, `localhost:3000` | that host **and** port |
| `*.example.com` | any sub-domain (`a.example.com`, `a.b.example.com`); **not** `example.com`, not `evilexample.com` |

Refused at validation time: `*`, `*.com`, paths, userinfo (`a@b`), bad ports, underscores, IP wildcards, other schemes.
Admin / serializer show the offending entry. Invalid legacy entries are ignored at runtime (they authorise nothing) and listed by
`manage.py report_projects_domain_status`.

## What is enforced, where
| call | policy |
|---|---|
| `POST /widget/init/` (creates a session) | `Origin` must match; **no `Origin` ⇒ 403 `origin_required`** when domains are configured |
| widget REST with a valid session (`start`, history, branding, mark_read, rate, upload, `ws-ticket`, KB feedback) | `Origin` must match **if present**; absent is tolerated |
| widget WebSocket (legacy and ticket) | `Origin` must match the **session's own project** (not just any project); re-checked live — removing a domain closes sockets opened from it |
| inactive project or workspace | refused everywhere (400 on init, 401 `session_invalid` afterwards, socket closed) |
| project with **no** domains | unrestricted (compatible) unless `WIDGET_REQUIRE_ALLOWED_DOMAINS=1`, then 403 `no_domains_configured` |

### Origin-less requests — the deliberate decision
Browsers always send `Origin` on a cross-site `fetch`/WebSocket, so a missing header means a non-browser client
(server-to-server, curl, a native app) or a same-origin GET. Such a caller can forge any `Origin`, so the domain list can
never be *authentication* — it only stops **other websites' browsers** from using a project key. Hence: creating a session
(the unauthenticated step) requires a verifiable `Origin`; calls that already carry a valid session credential are authorised
by that credential and merely tolerated without an `Origin`. Server-to-server identity for host applications uses the signed-assertion
path (SSO), not widget init. `Origin: null` (sandboxed iframes, `file://`) never matches.

## CORS and the WebSocket handshake follow the configured domains
Previously a customer's own store domain had to be added to the global `CORS_ALLOWED_ORIGINS` environment list and the app
restarted. Now a preflight / handshake for the **widget** endpoints (`/api/v1/widget/`, `/api/v1/kb/public/`, `/ws/widget/`,
`/ws/v2/widget/`) also accepts an origin configured on any active project (cached 60 s). That only lets the browser read the
response; the per-project check above is the real gate. Dashboard/admin/ticket endpoints are **never** opened this way, and the
static list keeps working unchanged.

## Rollout (nothing is changed on a live server by this PR)
1. Deploy. Behaviour for projects with empty domains is unchanged.
2. `python manage.py report_projects_domain_status` — fill every active project's domains (for tenants provisioned by an integration this happens automatically from `verified_domains`; to do it by hand:
   `projects.domains.sync_allowed_domains(project, verified_hostnames)` — idempotent, never removes manual entries).
3. Set `WIDGET_REQUIRE_ALLOWED_DOMAINS=1`. Rollback: unset it.

Compatibility risk to check before step 3: a store embedding the widget from a domain not yet listed gets 403 `origin_not_allowed`
on init. The widget logs the failure and stays hidden/offline; no data is affected.
