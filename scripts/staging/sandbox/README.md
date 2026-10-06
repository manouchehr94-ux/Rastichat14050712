# Sandbox staging harness (no Docker)

**What this is.** The full verification matrix for a release candidate, runnable in a single throw-away Linux box/container that has no
Docker daemon: local nginx with TLS on `*.example.test` names (mapped to 192.0.2.2 in `/etc/hosts`, signed by a local CA), its own
PostgreSQL and Redis, two Daphne workers behind a layer-4 balancer, the two Next.js dashboards, and the Playwright suite in
`e2e/staging-django52/`. Nothing here talks to a real host: no VPS, no production DB/nginx, no RastiSi. When a script says "apply to the
live nginx config and reload" it means the nginx *inside this sandbox*.

**What is sandbox-only (everything in this directory).** These scripts are tied to the box layout (ports 8100-8102/3100/3101, the
`/etc/nginx` files installed by `scripts/nginx/install-sites.sh`, `/srv/stg/{media,static}`, fuser-based stop). They are committed so that
the result of the matrix is reproducible, not because they are deployment tooling. **Nothing here is used by production.**

**What is NOT sandbox-only** (lives in `e2e/staging-django52/`, runs against any staging): the specs, `helpers.ts`, and the
`widgetSocketGate` per-page outage helper. `10-live.spec.ts` and `harness.ts` need two hooks (`STG_DJ` = a script that runs Python in the
Django shell from stdin, `STG_BALANCER_PIDFILE` = a balancer that understands SIGUSR1/SIGUSR2 for outage/restore); they skip themselves
when the hooks are absent.

## Quick start (fresh box)
```bash
export SANDBOX_DIR=/tmp/sbx VENV=/tmp/venv           # venv = python with backend/requirements.txt (+ requirements-dev.txt)
bash scripts/staging/sandbox/setup.sh                # once: CA/TLS, hosts, DB, env files, dashboards, nginx, seed, test integrations
bash scripts/staging/sandbox/start.sh                # 2 Daphne workers + balancer + dashboards + widget server + nginx
SI_PYTHON=<rastisi venv python> RASTISI_DIR=<rastisi checkout at the PR tip> bash scripts/staging/sandbox/run-matrix.sh <label> [soak-minutes]
```
`setup.sh` is idempotent and reproducible. Two details learned the hard way: (1) the `*.example.test` names resolve to **192.0.2.2** (an alias
on `lo`), not 127.0.0.1 — recent Chromium blocks requests from a "public" page (the fake embedding origins) to loopback, which hangs every browser
test that embeds the widget; (2) Chromium trusts the sandbox CA through the NSS database (`certutil`), the specs run with `ignoreHTTPSErrors:false`.

## What the matrix covers (stage names in `$SANDBOX_DIR/evidence/final/<label>/SUMMARY.md`)
| Stage | Proves |
|---|---|
| 00 preflight, 01 empty-DB migrate, **01b upgrade-style migration** | settings guardrails; fresh DB; synthetic EXISTING data created by the previous release (`origin/main`) survives the new migrations, new code works on it, every new migration is reversible and re-applies |
| 02 backend suite, 02b security checks, 03 JS (unit/lint-gate/typecheck/build), 03b widget, 04-05 nginx tests | CI-equivalent gates: migrations drift, Bandit, pip-audit, secret scan, vitest, ESLint baseline gate, builds, private-attachment routing + Range, log redaction |
| 06 health, 06b login limiter | stack up behind nginx TLS; the real limiter is proven before it is relaxed for the browser suites |
| 07 `staging-django52` Playwright (3 device projects) | WS tickets, reconnect + history resync, allowed domains/CORS/Origin, visitor sessions, attachments, live multi-worker + revocation, RTL/mobile |
| **07b `integration-staging`** | Integration Contract over real HTTPS/WSS behind nginx: idempotent provisioning, expired/replayed/wrong-audience/issuer/integration/forged/tampered/alg-confusion assertions, disabled integration, tenant + multi-store isolation, tenant admin ⇄ platform, platform → tenant first message (idempotent), close/reopen, WebSocket across both workers, live revocation, suspend/archive |
| **07c reference host on staging** (`STACK=staging e2e/reference-host/run.sh`) | a non-RastiSi host: icon-only / one-question / structured pre-chat, guest + trusted customer, staff SSO, headless path, mobile |
| **07d RastiSi cross-system on staging** (`STACK=staging e2e/rastisi/run.sh`) | the three RastiSi directions, two stores, multi-store owner, operator without admin rights, wrong store, flag off, revocation — the RastiSi adapter talking to the staging RastiChat over TLS |
| 07e cool-down, 08 soak, 09 log review, 10 rollback rehearsal, 11 final health | memory/ticket-key/unexpected-close checks; no asyncio/event-loop errors, no credentials in any log; roll back to the previous release and forward again |

## State directory
`export SANDBOX_DIR=<dir>`; it must hold `stg/` with: `backend.env` (Django env, `export` lines; DB `rasti_stg`, Redis db 5,
`ENVIRONMENT=staging`, throw-away secrets), `env.staging` (input for `install-sites.sh`), `ca.crt` (the local CA), `keys.json`
(`{"projectA": "<widget project key>"}`, written by `seed.py`), and the built dashboards `operator-app/`, `platform-app/` (Next standalone,
`NEXT_PUBLIC_API_BASE_URL=https://chat-stg.example.test/api/v1`). Logs, PIDs and evidence are written below it.
`VENV` (default `/tmp/venv`) is the Python venv with `backend/requirements.txt`.

## Usage
`bash run-matrix.sh <label> [soak-minutes]` runs: preflight, empty-DB migrate, backend tests, JS tests + typecheck, nginx media-routing and
log-redaction tests, stack health, enforced login limiter, Playwright (3 projects, `--retries=0`), cool-down, soak (40 visitor + 10
dashboard sockets, memory / ticket-key / unexpected-close checks), log review (`logcheck.sh`), rollback rehearsal, final health. Evidence goes to
`$SANDBOX_DIR/evidence/final/<label>/` (`SUMMARY.md` says GREEN/RED).

Deliberate adaptations, all visible in `run-matrix.sh`:
- The nginx login limit (10 r/min per address) is enforced and proven by `loginlimit.sh` first, then relaxed to 600 r/min **only** during the
  Playwright stage (the suite legitimately logs in more than 10 times a minute from one address) and restored afterwards.
- The soak binds its sockets to 5 loopback source addresses because nginx allows 20 WebSockets per IP.
- Network outages are simulated in `tcp-balancer.mjs` (Chromium's offline mode does not close an open WebSocket).
- Daphne is started as `python -m config.daphne_server` (the production entrypoint); the log review treats any credential in the Daphne
  access log as a failure.
