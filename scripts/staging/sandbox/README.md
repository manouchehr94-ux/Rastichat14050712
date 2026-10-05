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
