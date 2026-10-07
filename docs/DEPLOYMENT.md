# Deployment

A generic guide to running RastiChat on infrastructure you control. It does not assume any particular host, cloud or hosting company.
The repository ships working building blocks (Dockerfiles, a production compose file, nginx templates, deploy/rollback scripts); this
page explains how they fit together and what *must* be true of any deployment, however you assemble it.
The step-by-step Docker Compose + nginx + Let's Encrypt recipe is in [`runbooks/STAGING_DEPLOYMENT.md`](runbooks/STAGING_DEPLOYMENT.md).

> Nothing in this repository deploys itself. `scripts/staging/*` and the manual `staging-deploy.yml` workflow act only on the machine /
> secrets **you** configure, and only when a person starts them.

## 1. Components

| Component | What it is | Notes |
|---|---|---|
| **Backend** | Django 5.2 + DRF + Channels, served by **Daphne** (ASGI) | HTTP **and** WebSocket on the same port; one image, several roles (`web`, `migrate`, `collectstatic`, `check-deploy`, `automation-worker`, `sla-worker`) selected by `backend/docker-entrypoint.sh` |
| **PostgreSQL 15+** | system of record | the only supported database |
| **Redis 7+** | Channels layer, single-use WebSocket tickets, integration-token replay protection, rate limits, event counters | required for correctness, not just performance: with Redis down, realtime stops and integration calls fail closed (`503`) |
| **nginx** | TLS termination, rate limits, WebSocket upgrade, private attachment streaming, static files | templates in `deploy/nginx/`, installed by `scripts/nginx/install-sites.sh` |
| **Widget** | static `widget.js` bundle | built from `packages/widget`; served from the backend's domain as `/widget.js` and `/widget/<version>/widget.js` |
| **Operator dashboard** | Next.js standalone server, base path `/admin` | its own domain |
| **Platform dashboard** | Next.js standalone server, base path `/platform` | its own domain |
| **Schedulers** | `automation-worker` and `sla-worker` loops (`docker-scheduler-loop.sh`) | safe to run as several replicas (rows are claimed with `SELECT … FOR UPDATE SKIP LOCKED`) |

Three public hostnames are expected (`BACKEND_DOMAIN`, `OPERATOR_DOMAIN`, `PLATFORM_DOMAIN`); the widget is served from the backend's.

## 2. Topology and scaling

```
 internet ─► nginx (TLS) ─┬─► Daphne worker 1 ─┐
                          ├─► Daphne worker 2 ─┼─► PostgreSQL
                          └─► …                ┴─► Redis
          ─► operator dashboard (Next)   ─► (calls the backend's public API)
          ─► platform dashboard (Next)   ─► (calls the backend's public API)
```

* **Scale horizontally.** Daphne is a single asyncio process; add more *processes/containers* rather than threads. Any worker can serve
  any request or socket: shared state (channel layer, tickets, sessions, replay cache) lives in Redis and PostgreSQL, so **no sticky
  sessions are required**. This is verified with two workers behind a random balancer, including reconnects that land on a different worker.
* The shipped nginx site templates `proxy_pass` to one local port (`BACKEND_PORT`). To run several workers, declare an `upstream { server
  127.0.0.1:8101; server 127.0.0.1:8102; }` block and point the `proxy_pass` lines at it; keep `proxy_read_timeout`/`proxy_send_timeout`
  for `/ws/` long (the templates set 3600 s) because idle support chats are legitimate.
* PostgreSQL connections: each worker keeps persistent connections (`DB_CONN_MAX_AGE`, default 60 s); size `max_connections` for
  workers × concurrency.
* Run **one** `migrate` job per release, before the new workers start (see §6 and [`UPGRADE.md`](UPGRADE.md)). The `web` role never
  migrates by itself, specifically so a multi-replica rollout cannot race.

## 3. Configuration

All configuration is environment variables, validated at start-up: in `staging`/`production` the process **refuses to start** on
`DEBUG=1`, a missing/short secret key, wildcard `ALLOWED_HOSTS`, missing `MONITORING_TOKEN`, or unsafe legacy-credential settings.

* Templates (no secrets): `.env.production.example`, `.env.staging.example`, `.env.example` (local development).
* Reference of every variable: [`runbooks/ENVIRONMENT_VARIABLES.md`](runbooks/ENVIRONMENT_VARIABLES.md).
* Generate secrets with `scripts/generate-secrets.sh`. **Never** commit a real `.env`, and keep `chmod 600` on it.
* Minimum for production: `ENVIRONMENT=production`, `DEBUG=0`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`
  (dashboard origins), `CORS_ALLOWED_ORIGINS` (dashboards + every site that calls the API from a browser; widget origins are
  additionally derived from each project's allowed domains), `DATABASE_URL` or `DB_*`, `REDIS_URL` or `REDIS_*`, `MONITORING_TOKEN`,
  `MEDIA_ROOT`, `STATIC_ROOT`.
* **Dashboards bake the API/WebSocket URLs at build time** (`NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_WS_BASE_URL`): a different URL means a rebuild.
* `INTEGRATION_TOKEN_AUDIENCE` should differ between environments so a token minted for staging is useless in production.

Run the deploy gate before every release (also available as the `check-deploy` image role):

```bash
python manage.py check --deploy --fail-level WARNING --tag security
```

## 4. TLS and nginx

* Terminate TLS at nginx (`ssl-params.conf`; HSTS and security headers are set once, by nginx **and** Django without duplicating —
  `proxy-params.conf` hides the upstream copies).
* `scripts/nginx/install-sites.sh <env file>` renders the templates, writes **only** RastiChat's own sites, runs `nginx -t` and
  never reloads on failure; `issue-certs.sh` requests certificates (HTTP-01) once DNS is correct; run `install-sites.sh` again afterwards
  to switch from the HTTP bootstrap config to full HTTPS. Use your own certificate tooling if you prefer; the templates only need
  `fullchain.pem`/`privkey.pem` paths.
* The http-context files in `deploy/nginx/conf.d/` (rate-limit zones, WebSocket `Connection` map, **log redaction format**) must be
  included from `nginx.conf` (`include /etc/nginx/conf.d/*.conf;`). The installer warns if they are not.
* Edge limits (login 10/min, WebSocket handshake 30/min, general 40 r/s with burst) are per client address. Behind a corporate NAT or a
  CDN, make sure nginx sees the real client address (`real_ip` module) or those limits will be shared by everyone behind the proxy.
* `client_max_body_size 20m` must stay in step with `DATA_UPLOAD_MAX_MEMORY_SIZE`.

## 5. Static assets and private attachments

* `collectstatic` writes to `STATIC_ROOT`; nginx serves `/static/` from the same path (immutable caching).
* **Chat attachments are private.** Django authorises every fetch of `/api/v1/attachments/<message>/?sig=…` and answers
  `X-Accel-Redirect: /protected-media/<file>`; nginx streams the file (Range requests work) from an `internal` location that the
  internet cannot request directly. Everything else under `/media/` is `404` except public knowledge-base attachments.
  `ATTACHMENT_SERVE_MODE=accel` (default on staging/production) **requires** this nginx location; `django` mode is for development only
  (no Range). Details: [`runbooks/PRIVATE_ATTACHMENTS.md`](runbooks/PRIVATE_ATTACHMENTS.md).
* `MEDIA_ROOT` must be shared by every backend worker and readable by nginx (bind mount or shared volume). Back it up together with
  the database.
* Optional malware scan hook: `MEDIA_UPLOAD_SCAN_HOOK` (dotted path). See [`security/MEDIA_UPLOAD_SECURITY.md`](security/MEDIA_UPLOAD_SECURITY.md).

## 6. Release procedure (any orchestrator)

1. **Back up** PostgreSQL and `MEDIA_ROOT` ([`runbooks/BACKUP_RESTORE.md`](runbooks/BACKUP_RESTORE.md)); record the running image tags / git SHA.
2. **Validate** configuration: `check --deploy …` against the *production* environment variables.
3. **Build** images (backend, widget, both dashboards) from one commit; tag with the immutable git SHA as well as any moving tag.
4. **Migrate once**: `python manage.py migrate --noinput` (the `migrate` image role). Migrations follow the *expand/contract* rule, so the
   previous release keeps working on the migrated schema ([`UPGRADE.md`](UPGRADE.md)).
5. **Start/roll** workers: backend, then schedulers, then dashboards and the widget. Roll replicas one at a time; a socket on an old
   worker closes when that worker stops and the clients reconnect (new ticket, history resync) onto the new one.
6. **Check** `/api/v1/health/ready/` (all components up), then the smoke checks in [`OPERATIONS.md`](OPERATIONS.md).
7. nginx changes are separate: `install-sites.sh` validates before reloading.

`scripts/staging/deploy.sh <env file>` performs steps 1–7 for the Docker Compose layout; `scripts/staging/rollback.sh <git sha>` retags
the previous images and restarts them. Both stop at the first failing step. If you use Kubernetes, Nomad or systemd instead, keep the
same order: *migrate once → backend → schedulers → front ends*.

## 7. Health checks

| Endpoint | Use it for |
|---|---|
| `GET /api/v1/health/live/` | process liveness (container restart decisions); never fails on a DB/Redis outage |
| `GET /api/v1/health/ready/` | traffic routing: `503` when the database, Redis or migrations are not OK (anonymous callers see up/down flags, not error text) |
| `GET /api/v1/health/monitoring/` + `X-Monitoring-Token` | scheduler heartbeats, disk, backup freshness, integration counters ([`OPERATIONS.md`](OPERATIONS.md)) |

## 8. Rollback principles

* **Roll back code, not the schema.** Previous images run on the migrated database (new `NOT NULL` columns carry database defaults;
  this is tested). Reverse migrations only deliberately — they lose data written to new columns/tables.
* Fastest *feature* rollbacks need no deploy: disable one tenant (`PUT … status: suspended`), disable an integration
  (`integration_set_active --disable`), or revoke a key (`integration_key_revoke`).
* `rollback.sh --restore-db` restores a backup and is destructive: separate, explicit, opt-in.
* Never `docker compose down -v` as part of a rollback; it destroys the data volumes.

Generic and integration-specific rollback tables: [`runbooks/DEPLOYMENT_ROLLBACK.md`](runbooks/DEPLOYMENT_ROLLBACK.md),
[`runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md`](runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md).

## 9. Hardening checklist

- [ ] `ENVIRONMENT=production`, `DEBUG=0`, deploy gate green
- [ ] PostgreSQL and Redis **not** reachable from the internet; Redis requires a password
- [ ] nginx log-redaction format active (no credentials in access logs: run `scripts/nginx/test-log-redaction.sh`)
- [ ] `/protected-media/` is `internal`; `scripts/nginx/test-media-routing.sh` passes
- [ ] real client address visible to nginx (for the per-address limits)
- [ ] `MONITORING_TOKEN` set and kept out of dashboards and logs
- [ ] backups run and `backup.stale` is false in the monitoring endpoint
- [ ] host integration keys are held by the *hosts*; RastiChat stores public keys only
- [ ] `LEGACY_URL_CREDENTIALS_*` and `WIDGET_ALLOW_UNVERIFIED_EXTERNAL_ID` **unset** ([`WS_TICKETS_AND_URL_CREDENTIALS.md`](runbooks/WS_TICKETS_AND_URL_CREDENTIALS.md))
