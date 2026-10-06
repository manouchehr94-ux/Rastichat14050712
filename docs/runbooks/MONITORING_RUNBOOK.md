# Monitoring Runbook

## Health endpoints

| Endpoint | Purpose | Never reports "up" when... |
|---|---|---|
| `GET /api/v1/health/live/` | Liveness — is the process alive at all. | Never fails from a DB/Redis outage — only a genuinely wedged process. Use for container-restart decisions. |
| `GET /api/v1/health/ready/` | Readiness — should traffic route here. | DB unreachable, Redis unreachable, or unapplied migrations exist. Returns 503, not 200, in any of those cases. Use for load-balancer/proxy routing decisions. |
| `GET /api/v1/health/monitoring/` | Operational visibility (not a traffic gate). | Reports scheduler staleness, disk usage, backup freshness — see below. **Requires `X-Monitoring-Token: $MONITORING_TOKEN`** (a bare 401, no detail, without it — disk/backup/scheduler info is not for anonymous callers). |
| `GET /api/v1/health/` | Legacy, backward-compatible shape (`{"status": "healthy"/"unhealthy", ...}`). | Same DB/Redis checks as readiness, without migration-state detail. |

`/health/ready/`'s `error` detail strings (raw DB/Redis exception text) are
also only ever included for a caller presenting the same
`X-Monitoring-Token` header — an anonymous caller (a load balancer, say)
still sees `up: true/false`, just not *why* it's false.

```bash
curl -fsS https://chat-staging.rastisi.ir/api/v1/health/ready/ | python3 -m json.tool
# With detail, as an authorized monitoring caller:
curl -fsS -H "X-Monitoring-Token: $MONITORING_TOKEN" \
  https://chat-staging.rastisi.ir/api/v1/health/ready/ | python3 -m json.tool
```

## Reading `/health/monitoring/`

```bash
curl -fsS -H "X-Monitoring-Token: $MONITORING_TOKEN" \
  https://chat-staging.rastisi.ir/api/v1/health/monitoring/ | python3 -m json.tool
```

```json
{
  "schedulers": {
    "automation-worker": {"seen": true, "stale": false, "last_status": "SUCCESS", "age_seconds": 12},
    "sla-worker": {"seen": true, "stale": false, "last_status": "SUCCESS", "age_seconds": 8}
  },
  "disk": {"percent_used": 42.1, "warning": false, "free_bytes": 123456789},
  "backup": {"found": true, "stale": false, "newest_file": "rastichat-db-20260101-030000.sql.gz", "age_hours": 4.2}
}
```

- `schedulers.*.stale = true` — that scheduler hasn't recorded a
  heartbeat within 5x its configured interval. Check
  `docker compose logs automation-worker` / `sla-worker`.
- `disk.warning = true` — usage above `DISK_USAGE_WARNING_PERCENT`
  (default 85%). Free space or grow the volume.
- `backup.stale = true` — no backup within `BACKUP_MAX_AGE_HOURS`
  (default 26h). Run `scripts/staging/backup.sh` and check the cron/timer
  that should be doing this automatically.

## Integration event counters

`/health/monitoring/` also returns `events_last_24h`: counters for the integration platform (and WebSocket authentication),
bucketed per UTC day in Redis for 14 days. Names are `<event>` or `<event>:<label>`:

| Counter | Meaning / what to do |
|---|---|
| `token_refused:<code>` | a host token was refused (`replay`, `invalid_token`, `token_expired`, `binding_mismatch`…). A burst of `replay`/`invalid_*` = attack or clock skew between host and RastiChat; check NTP first |
| `scope_denied` | a valid key tried something outside its scopes — a host misconfiguration or a stolen key probing |
| `cross_tenant_denied:<code>` | a request tried to use a tenant it does not own (`tenant_mismatch`, `tenant_unavailable`…). Any non-trivial count deserves a look at the integration |
| `rate_limited` | an integration or exchange throttle fired |
| `identity_exchange:customer` / `:staff_*` | successful SSO exchanges (baseline for the refusals above) |
| `audit:<action>` | every audited operation (tenant provisioned/updated/status changed, role mapped, identity created/disabled, guest upgraded, context updated…) |
| `conversation_created:customer`, `prechat_submitted`, `support_thread:started|resumed` | product activity |
| `ws_ticket_issued:<kind>`, `ws_auth:ok|refused` | realtime connects/reconnects; `refused` spikes = expired/replayed tickets or revocation |
| `widget_config:served` | widget initialisations |

The same events are written one per line to the application log as
`rastichat_event event=<name> label=<code> k=v …` (never a credential: credential-named fields are dropped).

## Container-level status

```bash
scripts/staging/status.sh .env.staging
```

Prints `docker compose ps`, the backend image's git-SHA label, all
three health endpoints, and the latest backup filename.

```bash
docker stats --no-stream       # CPU/RAM per container, right now
docker compose logs -f backend automation-worker sla-worker   # tail logs
```

## Redis restart / reconnect (manual test — see also
`docs/testing/STAGING_MANUAL_QA.md` scenario 19)

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging restart redis
```

Expected: existing WebSocket connections drop and the Widget/dashboard
reconnect on their own (both already implement reconnect-on-close);
`/api/v1/health/ready/` briefly reports `redis.up: false` then recovers
once Redis is back. No cross-workspace message leakage after
reconnect — verify by having two different workspaces' conversations
open in separate browser tabs during the restart.

## Container restart persistence (manual test — scenario 20)

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging restart backend
```

Expected: in-flight requests fail during the restart window, then
succeed again once healthy; no data loss (Postgres/Redis/media are on
named volumes, not container-local storage) — verify a message sent
just before the restart is still visible in the conversation history
just after.

## Logs

Everything goes to stdout/stderr (12-factor — no log files inside
containers):

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging logs --since 1h backend
```

Every log line includes a request-correlation ID (`req=<id>`) —
`X-Request-ID` from Nginx if present, generated otherwise, echoed back
in the response's `X-Request-ID` header so a customer/operator-reported
issue can be traced end-to-end. No passwords, JWTs, or full message
bodies are ever logged (see `common/middleware.py`).

## Load baseline

```bash
BASE_URL=https://chat-staging.rastisi.ir WS_URL=wss://chat-staging.rastisi.ir/ws \
PROJECT_KEY=<Project.public_key> VISITOR_COUNT=50 node scripts/staging/load-baseline.mjs
```

While it runs, capture server-side numbers by hand (the script only
sees the outside):

```bash
docker stats --no-stream
free -h
docker compose exec db psql -U rastichat -c "SELECT count(*) FROM pg_stat_activity;"
docker compose exec redis redis-cli info memory | grep used_memory_human
```

The script reports message round-trip latency (p50/p95/p99), duplicate/
lost message counts (by `client_message_id`), upload success rate, and
overall error rate. **This is a factual baseline for that one run
against that one VPS at that one traffic level — never a production
capacity claim.** A burst of `start 429`s is very likely the load
script's single-source-IP artifact tripping the per-IP `widget_start`
rate limit (real, IP-diverse visitors wouldn't hit this the same way),
not evidence of a capacity problem — the script prints this caveat on
every run.
