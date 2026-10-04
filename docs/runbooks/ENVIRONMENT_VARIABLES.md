# Environment Variables

Full reference for `.env.example` / `.env.staging.example` /
`.env.production.example`. Defined and enforced in `backend/config/settings.py`
unless noted otherwise.

## Identity / core Django

| Variable | Required in staging/prod | Default | Notes |
|---|---|---|---|
| `ENVIRONMENT` | yes | `development` | `development` \| `staging` \| `production`. Drives every guardrail below. |
| `DEBUG` | must be `0` | `1` in dev, `0` elsewhere | Refuses to start if `1` while `ENVIRONMENT` is staging/production. |
| `DJANGO_SECRET_KEY` | yes | none (dev falls back to a fixed insecure key) | Generate with `scripts/generate-secrets.sh`. `SECRET_KEY` also accepted (back-compat with the dev `docker-compose.yml`). |
| `DJANGO_SETTINGS_MODULE` | no | `config.settings` | Same module for every environment — behavior is env-var-driven, not file-per-environment. |
| `ALLOWED_HOSTS` | yes | `*` in dev | Comma-separated hostnames, no scheme. Refuses `*` or empty in staging/prod. |
| `CSRF_TRUSTED_ORIGINS` | yes | empty | Comma-separated, full scheme+host (e.g. `https://operator-chat-staging.rastisi.ir`). |
| `CORS_ALLOWED_ORIGINS` | yes | empty (falls back to allow-all in dev only) | Comma-separated, full scheme+host — every page that embeds the Widget or calls the API from a browser, including the Rastisi storefront. |
| `TIME_ZONE` | no | `UTC` | |
| `DJANGO_LOG_LEVEL` | no | `INFO` | Applies to this app's own loggers only, not third-party libraries (see `common/middleware.py`/settings `LOGGING`). |
| `ADMIN_URL` | no | `admin/` | Move the Django admin off the well-known path if desired. |

## Database

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `postgres://user:pass@host:port/name` — takes priority over the `DB_*` vars below if set. |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | Used if `DATABASE_URL` is unset. Staging/prod require one or the other. |
| `DB_CONN_MAX_AGE` | Default `60` (seconds) — persistent connections. |

SQLite is never used outside test runs. Postgres only.

## Redis

| Variable | Notes |
|---|---|
| `REDIS_URL` | `redis://[:password@]host:port/db` — takes priority over `REDIS_HOST`/etc. |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` | Used if `REDIS_URL` is unset. |

Backs both the Channels/WebSocket layer and the WS-level rate limiter
(`common/ws_throttling.py`) — there is no separate cache backend.

## Media / static

| Variable | Notes |
|---|---|
| `MEDIA_ROOT` / `STATIC_ROOT` | In-container paths the Django process itself uses. |
| `MEDIA_HOST_PATH` / `STATIC_HOST_PATH` | Host paths backing the same data — `docker-compose.staging.yml`'s bind-mounted volumes AND what `deploy/nginx/sites/backend.conf.template` serves `/media/`/`/static/` from directly (Django's DEBUG-only static-file helpers never run in staging/production). |
| `MEDIA_UPLOAD_MAX_IMAGE_BYTES` / `MEDIA_UPLOAD_MAX_VOICE_BYTES` | Per-attachment-type caps enforced in `conversations/media_validation.py`. |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | Django's own total-request-body ceiling — keep in sync with Nginx's `client_max_body_size`. |
| `MEDIA_UPLOAD_SCAN_HOOK` | Optional dotted path to a `callable(file) -> bool` malware-scan hook. Unset by default. |

## Rate limiting

Each is a DRF throttle rate string (`N/min`, `N/hour`, ...), applied via
`ScopedRateThrottle` on the named endpoint (see `config/settings.py`
`DEFAULT_THROTTLE_RATES` for the exact scope->endpoint mapping):

`LOGIN_THROTTLE_RATE`, `WIDGET_START_THROTTLE_RATE`,
`WIDGET_MESSAGE_THROTTLE_RATE`, `WIDGET_RATING_THROTTLE_RATE`,
`KB_FEEDBACK_THROTTLE_RATE`, `KB_SEARCH_THROTTLE_RATE`,
`MACRO_EXECUTION_THROTTLE_RATE`, `MEDIA_UPLOAD_THROTTLE_RATE`.

`WIDGET_WS_MESSAGE_RATE_LIMIT` / `WIDGET_WS_MESSAGE_RATE_WINDOW_SECONDS`
govern the separate Redis-backed WebSocket message-send limiter
(`common/ws_throttling.py`) — a REST throttle never runs on a Channels
consumer, so this exists specifically to close that gap.

All are disabled automatically under `manage.py test` (see `TESTING` in
`config/settings.py`) — DRF's throttle counters live in Django's
process-global cache, which isn't reset between test methods.

## Security headers

| Variable | Notes |
|---|---|
| `SECURE_HSTS_SECONDS` | Default `31536000` (1 year) in staging/prod. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | Default on in staging/prod. |
| `SECURE_HSTS_PRELOAD` | Default **off** even in staging/prod — submitting to the browser preload list is hard to reverse (removal takes months); opt in explicitly once every subdomain is permanently HTTPS-only. |
| `CONTENT_SECURITY_POLICY` | Full CSP header value; has a conservative same-origin-only default (see `common/middleware.py`). |

## Schedulers / monitoring

| Variable | Notes |
|---|---|
| `AUTOMATION_WORKER_INTERVAL_SECONDS` / `SLA_WORKER_INTERVAL_SECONDS` | Loop interval for `docker-scheduler-loop.sh`; also used by `common/views.py:MonitoringView` to judge heartbeat staleness (5x interval). Defaults `30`/`60`. |
| `BACKUP_DIR` | Default `/opt/rastichat/backups` (host) — read by `scripts/staging/backup.sh` and the monitoring endpoint's backup-freshness check. |
| `BACKUP_MAX_AGE_HOURS` | Default `26` — when the monitoring endpoint flags the last backup as stale. |
| `BACKUP_RETENTION_DAYS` | Default `14` — how long `backup.sh` keeps old backups before deleting them. |
| `DISK_USAGE_WARNING_PERCENT` | Default `85`. |

## Domains and ports (consumed by scripts, not by Django directly)

| Variable | Notes |
|---|---|
| `BACKEND_DOMAIN` / `OPERATOR_DOMAIN` / `PLATFORM_DOMAIN` | Used by `scripts/nginx/install-sites.sh` and `scripts/nginx/issue-certs.sh` to render the Nginx templates and request certificates. Should match the domains actually listed in `ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS` above. |
| `CERTBOT_EMAIL` | Registered with Let's Encrypt for renewal notices only. |
| `BACKEND_PORT` / `OPERATOR_PORT` / `PLATFORM_PORT` / `WIDGET_PORT` | Host-side ports `docker-compose.staging.yml` publishes to `127.0.0.1` and Nginx proxies to. Change if `scripts/staging/preflight.sh` reports a conflict. |

## Frontend build-time (Next.js / Widget)

| Variable | Notes |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` / `NEXT_PUBLIC_WS_BASE_URL` | Baked into the Next.js bundle at `docker build` time (build args — see `docker/*.Dockerfile`), NOT read at container runtime. Changing them requires rebuilding the operator/platform-dashboard images. The Widget itself takes these as runtime `RastiChat.init({apiBase, wsBase})` config instead — no rebuild needed for a new embedding site. |

## Docker image build only

| Variable | Notes |
|---|---|
| `IMAGE_TAG` | Moving tag (`docker-compose.staging.yml` default `staging`) every service resolves to. `scripts/staging/deploy.sh` also tags each build with the immutable git SHA for `scripts/staging/rollback.sh` to retag from. |
| `GIT_SHA` | Backend image build arg — embedded as an OCI `org.opencontainers.image.revision` label, read by `scripts/staging/status.sh`. |
