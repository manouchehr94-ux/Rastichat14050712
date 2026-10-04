# RastiChat LAN One-Click Launcher

Run the full RastiChat stack (Postgres, Redis, Django backend, Widget,
Operator Dashboard, Platform Dashboard) on one Windows PC, bound to that
PC's real Wi‑Fi/LAN IP address, so other laptops and phones on the **same
router** can open it — no manual IP lookup, no editing source files, no
`.env.local` files to create by hand, no juggling multiple terminal windows.

This is a **local development/demo tool**. It is not a production deployment
mechanism and does not change any chat/assignment/automation/security logic —
it only starts the existing stack with LAN-reachable configuration.

## What you need first

- Windows 10/11 with PowerShell 5.1+ (already built in) or PowerShell 7+.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed
  and **running** (this launcher never installs it for you).
- [Node.js](https://nodejs.org/) 20+ and npm.
- Git (only needed to clone/update the repo).
- All test devices (this PC, phones, other laptops) connected to the **same
  Wi‑Fi router**, and that router's Wi‑Fi network set to **Private** in
  Windows network settings (a Public profile makes Windows Firewall block
  incoming LAN connections).

Run `Status-RastiChat-LAN.bat` is for checking a running stack; to check
prerequisites *before* starting, run `doctor` from a terminal (see below).

## Quick start

1. Clone or update the repo (`git clone ...` or `git pull`) anywhere on your
   PC — any drive, any folder, spaces in the path are fine.
2. Open the `tools\lan-launcher` folder and double-click
   **`Start-RastiChat-LAN.bat`**.
3. Wait — the first run builds the widget and Docker images, so it can take a
   few minutes. Subsequent runs are much faster.
4. When it finishes, a summary is printed **and** saved to
   `runtime\LAN-URLS.txt` (also opened as `runtime\index.html`), with:
   - the **Widget** link — open on a phone or another laptop to test as a
     customer;
   - the **Operator Dashboard** link — open on your laptop to answer as an
     agent;
   - the **Platform Dashboard** link;
   - working demo login accounts (see "Demo accounts" below).
   The Widget and Operator Dashboard tabs open automatically unless you pass
   `-NoBrowser`.
5. When you're done, double-click **`Stop-RastiChat-LAN.bat`**. Your data
   (conversations, messages, accounts) is preserved between stops/starts —
   only Docker containers and the frontend processes are stopped.

## The BAT files

Each one is a thin wrapper that just calls `RastiChat-LAN-Manager.ps1` with a
fixed command — all real logic lives in that one PowerShell script.

| File | Command | What it does |
|---|---|---|
| `Start-RastiChat-LAN.bat` | `start` | Builds/starts everything, prints LAN URLs, opens the browser. |
| `Stop-RastiChat-LAN.bat` | `stop` | Stops frontend processes + Docker containers. Keeps your data. |
| `Restart-RastiChat-LAN.bat` | `restart` | `stop` then `start`. |
| `Status-RastiChat-LAN.bat` | `status` | Shows what's running: PID, port, URL, health, uptime. |
| `Logs-RastiChat-LAN.bat` | `logs` | Dumps recent logs for every service. |

Any of these can also be run from a terminal with extra flags, e.g.:

```bat
Logs-RastiChat-LAN.bat -Service backend -Follow
Stop-RastiChat-LAN.bat -RemoveFirewallRules
```

## All commands (via PowerShell directly)

```powershell
cd tools\lan-launcher
powershell -ExecutionPolicy Bypass -File RastiChat-LAN-Manager.ps1 <command> [options]
```

| Command | Purpose |
|---|---|
| `start` | Full startup flow (default if no command given). |
| `stop` | Stop everything; add `-RemoveFirewallRules` to also drop the firewall rules. |
| `restart` | Stop, then start again. |
| `status` | Show current process/container state. |
| `logs [-Service backend\|widget\|operator\|platform] [-Follow]` | View logs. |
| `doctor` | Check prerequisites only — does not start anything. |
| `urls` | Reprint the current LAN URLs (regenerates config/restarts frontends if your Wi‑Fi IP changed since `start`). |
| `seed` | Re-run the deterministic demo-data seed against the running backend. |
| `smoke` | Run the automated LAN functional smoke test against the real running URLs (see below). |
| `reset -Confirm` | **Destructive.** Stops everything and deletes the Postgres/Redis Docker volumes. Requires the explicit `-Confirm` switch — there is no default-destructive path. |

Other flags: `-LanIP <ip>` to override automatic Wi‑Fi IP detection,
`-OpenPlatform` to also open the Platform Dashboard tab on `start`.

## Ports

| Service | Default port |
|---|---|
| Backend API | 8080 |
| Widget | 8081 |
| Operator Dashboard | 3100 |
| Platform Dashboard | 3101 |
| Postgres (this PC only, never exposed to the LAN) | 5433 |
| Redis (this PC only, never exposed to the LAN) | 6380 |

If a default port is already in use by something else, `start`/`doctor`
reports exactly what's occupying it (process name + PID) — it never silently
picks a different port for you.

## Demo accounts (local test data only — never use in production)

| Role | Email | Password |
|---|---|---|
| Workspace admin | `admin@ws.com` | `pass1234` |
| Operator 1 | `operator@ws.com` | `pass1234` |
| Operator 2 | `operator2@ws.com` | `pass1234` |
| Platform support | `support@platform.com` | `pass1234` |

These come from the repo's existing, idempotent `backend/seed_data.py` — the
launcher does not invent new accounts or passwords.

## Windows Firewall

`start` adds a small number of **Inbound, Private-profile-only** firewall
rules (one per port, named `RastiChat LAN - <service> (<port>)`), so phones
and other laptops on the same Wi‑Fi can actually reach the ports. Adding
these requires Administrator rights; if the launcher isn't already elevated
it opens one UAC prompt for just this step (you'll see a second PowerShell
window appear briefly — that's expected, it does only the firewall step and
closes). The rules are never added to the Public profile, and this launcher
never disables Windows Firewall as a whole.

To remove them: `Stop-RastiChat-LAN.bat -RemoveFirewallRules`, or manually
find the "RastiChat LAN Development" rule group in Windows Defender
Firewall with Advanced Security.

## Testing on multiple devices — manual checklist

Once `start` finishes and prints the URLs:

1. **Phone (Widget, as a customer)** — connect the phone to the same Wi‑Fi,
   open the Widget URL in its browser, open the chat panel, send a message.
2. **This laptop (Operator Dashboard, as an agent)** — log in with
   `operator@ws.com` / `pass1234`, find the phone's conversation, reply.
   Confirm the reply appears on the phone almost immediately (this exercises
   the websocket connection over your LAN IP, not just page load).
3. **A second device, in an incognito/private window (a second customer)** —
   open the Widget URL again; confirm it gets its own separate conversation
   and never sees the first customer's messages.
4. Refresh the phone's Widget page — confirm the conversation history is
   still there.

## Automated LAN smoke test

`smoke` drives the checklist above automatically with Playwright, against
the real LAN URLs `start` just printed (never `localhost`): widget loads
(HTTP 200), `window.RastiChat` initializes, sending a message creates a real
conversation, operator login works, replies deliver live in both directions
(so it fails if the websocket doesn't actually work over your LAN IP),
history survives a refresh, and no CORS errors were observed. Run it after
`start` has finished successfully:

```bat
RastiChat-LAN-Manager.ps1 smoke
```

The first run installs its own small set of dependencies
(`tools\lan-launcher\smoke\node_modules`, not committed to the repo). A full
HTML/JSON report is written under `tools\lan-launcher\runtime\`.

## Troubleshooting

- **Phone can't reach the Widget/Operator URL at all.**
  - Confirm the phone and PC are on the *same* Wi‑Fi network (not a guest
    network — some routers put guest Wi‑Fi behind AP/client isolation, which
    blocks device-to-device traffic entirely; there's no launcher-side fix
    for that, use the main network or disable client isolation on the
    router).
  - On the PC, confirm the Wi‑Fi network is set to **Private**, not Public
    (Settings → Network & Internet → Wi‑Fi → your network → Network
    profile type). `doctor` and `start` both warn about this.
  - Turn off any VPN on the phone or the PC — a VPN routes traffic away from
    the LAN and breaks reachability.
  - Re-run `status` — if a service shows "متوقف" (stopped) or isn't
    `READY`, check `logs -Service <name>`.
- **Wi‑Fi IP changed since I ran `start` (e.g. laptop slept and
  reconnected).** Run `urls` — it detects the mismatch, regenerates the
  runtime config, restarts the Operator/Platform dashboards on the new IP,
  and reprints working links. `status`/`Get-LanIPAddress` no longer matching
  what a phone can reach is the symptom to watch for.
- **A dashboard page loads but nothing works / API calls fail.** Hard-refresh
  the browser tab (the previous session's cached JS may still be pointing at
  an old IP — normal browser cache, not launcher-specific).
- **`start` fails at the "doctor" step.** Read the `[ERROR]` lines printed —
  each names exactly what's missing (Docker not running, Node not installed,
  a port already in use, etc.). Fix that one thing and re-run.
- **I ran `start` twice without `stop`.** That's handled automatically —
  the launcher stops its own previously-started frontend processes before
  starting new ones, so you won't get orphaned processes or port conflicts
  from that.

## What this launcher deliberately does NOT do

- Never edits any tracked source file. Every generated file (LAN IP, chosen
  ports, `.env.local` files, `docker-compose.lan.yml`, PIDs, logs) lives
  under `tools\lan-launcher\runtime\` or the target apps' own
  git-ignored `.env.local`, and is regenerated on every `start`.
- Never exposes Postgres or Redis to the LAN — only to `127.0.0.1` on this
  PC (see `docker-compose.lan.yml`, regenerated every run).
- Never installs Docker, Node, or Git for you.
- Never disables Windows Firewall, and never adds a Public-profile rule.
- Never touches production configuration, secrets, or deployment — this is
  a local developer/demo convenience only.

## Optional EXE wrapper

Not included. The task allowed one only if it could be a reproducible,
from-source build with no opaque third-party binaries, and only after the
PS1/BAT files were fully tested end-to-end on real Windows hardware with
real phones. Neither of those held in the environment this launcher was
built in (a Linux sandbox with no Docker daemon and no Windows runtime — see
the delivery report for exactly what could and couldn't be verified here),
so it was left out rather than shipped unverified. `Start-RastiChat-LAN.bat`
works fully without it.
