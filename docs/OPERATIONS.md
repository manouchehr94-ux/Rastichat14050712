# Operations

What to watch, where to look and what to do when something is wrong in a running RastiChat deployment.
Deeper reference: [`runbooks/MONITORING_RUNBOOK.md`](runbooks/MONITORING_RUNBOOK.md) (counters, load baseline),
[`runbooks/INCIDENT_CHECKLIST.md`](runbooks/INCIDENT_CHECKLIST.md), [`runbooks/BACKUP_RESTORE.md`](runbooks/BACKUP_RESTORE.md),
[`runbooks/INTEGRATION_PROVISIONING_SECURITY.md`](runbooks/INTEGRATION_PROVISIONING_SECURITY.md).

## 1. Health endpoints

| Endpoint | Auth | Meaning | Use for |
|---|---|---|---|
| `GET /api/v1/health/live/` | none | the process is alive; never fails because of a database/Redis outage | container restart decisions |
| `GET /api/v1/health/ready/` | none | `200` only if PostgreSQL, Redis **and** migrations are OK; otherwise `503 {"status":"not_ready","components":{…}}` (error text only with the monitoring token) | load-balancer / orchestrator routing |
| `GET /api/v1/health/monitoring/` | `X-Monitoring-Token` | scheduler heartbeats, disk usage, backup freshness, integration event counters | dashboards, alerts |
| `GET /api/v1/health/` | none | legacy shape (`healthy`/`unhealthy`) | old probes |

```bash
curl -fsS https://chat.example.com/api/v1/health/ready/
curl -fsS -H "X-Monitoring-Token: $MONITORING_TOKEN" https://chat.example.com/api/v1/health/monitoring/ | python3 -m json.tool
```

Keep the monitoring token out of dashboards, browsers and logs. Probes of `/api/v1/health/` are exempt from nginx's general rate limit.

## 2. What the monitoring endpoint reports

```json
{"schedulers": {"automation-worker": {"seen": true, "stale": false, "age_seconds": 12}, "sla-worker": {…}},
 "disk": {"percent_used": 42.1, "warning": false}, "backup": {"found": true, "stale": false, "age_hours": 4.2},
 "events_last_24h": {"token_refused:replay": 0, "identity_exchange:customer": 318, "ws_auth:ok": 1204, "ws_auth:refused": 3, "…": 0}}
```

* `schedulers.*.stale` — no heartbeat within 5× the interval → check the worker container's logs.
* `disk.warning` — above `DISK_USAGE_WARNING_PERCENT` (85 %): attachments grow `MEDIA_ROOT`.
* `backup.stale` — no backup within `BACKUP_MAX_AGE_HOURS` (26 h).
* `events_last_24h` — **structured counters** (day buckets in Redis, kept 14 days). Alert on bursts of: `token_refused:*` (especially `replay`,
  `invalid_token`, `token_expired` → attack, or clock skew between a host and the backend: check NTP first), `scope_denied`,
  `cross_tenant_denied:*` (any sustained count: look at that integration), `rate_limited`, `ws_auth:refused` (expired/replayed tickets or revocation).

The same events are one log line each: `rastichat_event event=<name> label=<code> k=v …` — credential-named fields are dropped and values sanitised.
There is no Prometheus endpoint in v1; the counters are exposed as JSON and are a few lines to export once you run a metrics stack
(see [`integrations/V1_SCOPE_AND_DEFERRALS.md`](integrations/V1_SCOPE_AND_DEFERRALS.md)).

## 3. Redis dependency

Redis is part of correctness. It holds: the Channels layer (realtime fan-out), WebSocket tickets, integration-token `jti` replay markers,
WebSocket message-rate counters, event counters, small caches.

| If Redis is down | Effect |
|---|---|
| realtime | sockets drop; new tickets cannot be issued (`503`); clients reconnect and resync by themselves once Redis is back |
| integration APIs and identity exchange | **fail closed**: `503 replay_store_unavailable` (log line `integration_replay_store_unavailable` — alert on this) |
| plain widget guest chat over REST | unaffected |
| `ready` | `503`, `components.redis.up=false` |

After a Redis restart verify no cross-tenant leakage by keeping two different tenants' conversations open in separate tabs during the
restart (manual scenario in [`testing/STAGING_MANUAL_QA.md`](testing/STAGING_MANUAL_QA.md)). Give Redis persistence only if you need the
event counters to survive restarts; nothing else requires it.

## 4. Backend workers

* Each Daphne process is independent; kill/replace one at a time. Its sockets close (`1001`/`1006`) and clients reconnect to another worker.
* Daphne's own healthcheck is `GET /api/v1/health/live/`.
* Scheduler loops (`automation-worker`, `sla-worker`) are idempotent and may run as several replicas.
* Look for `Exception inside application`/asyncio errors in worker logs after a deploy; a clean release has none (this is part of the staging
  log review).

## 5. Routine jobs (cron / systemd timers)

| Command | Cadence | Why |
|---|---|---|
| `scripts/staging/backup.sh` (or your own `pg_dump` + media copy) | daily | backups; freshness is monitored |
| `manage.py purge_expired_visitor_sessions [--older-than-days N]` | daily/weekly | removes dead session rows only; visitors and conversations are never deleted |
| `manage.py integration_purge_idempotency --older-than-hours 48` | daily | drops expired idempotency records |
| `manage.py report_projects_domain_status` | before enabling `WIDGET_REQUIRE_ALLOWED_DOMAINS` | lists projects without valid allowed domains |
| `manage.py report_legacy_credential_usage --days 14` | only during a legacy-credential exception window | read-only usage report |

## 6. Logs

* Application logs go to **stdout/stderr** (12-factor); collect them with your platform's log driver. Every line carries `req=<id>`; the id is
  echoed to the client as `X-Request-ID`, so a user-reported problem can be traced end to end. Level: `DJANGO_LOG_LEVEL`.
* nginx: `/var/log/nginx/rastichat-backend-access.log` (redacted format), `…-error.log`, and the operator/platform equivalents.
* Security-relevant log events: `integration_token_refused`, `integration_scope_denied`, `integration_replay_store_unavailable`,
  `rastichat_event event=ws_auth label=refused`.

### What must never appear in logs
Passwords; dashboard or integration JWTs; identity assertions; WebSocket tickets; visitor session tokens; signed attachment URLs/tokens;
host private keys; the monitoring token; full message bodies. nginx and Daphne access logs redact credential path segments and drop query
strings. Verify after any nginx change: `scripts/nginx/test-log-redaction.sh`; spot-check production logs with
`grep -E 'assertion=|ticket=|session_token=|sig=' /var/log/nginx/rastichat-*.log` → must return nothing.

## 7. Common failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `ready` = 503, `migrations.up_to_date=false` | release started before `migrate` | run the migrate job; never ship workers first |
| many `token_refused:token_expired`/`invalid_token` from one host | host clock skew or wrong audience | fix NTP; compare host `aud` with `INTEGRATION_TOKEN_AUDIENCE` |
| `token_refused:replay` spike | a host retries with the *same* token, or an attack | the host must mint a new token **per request and per retry** |
| `503 replay_store_unavailable` | Redis unreachable | restore Redis; integration calls resume automatically |
| operators see "failed to load" under load | per-address nginx limit shared by a NAT/CDN | configure `real_ip`; adjust `rastichat_general` in `conf.d/rastichat-limits.conf` |
| widgets get `403 origin_not_allowed` | the site's host is missing from the tenant's `verified_domains` | add the exact host (PUT tenant) |
| sockets close `4403` right after connect | membership/session/tenant revoked or allowed domain removed | expected; the user must re-authenticate |
| scheduler `stale` | worker crashed/stopped | restart; check DB connectivity |
| `disk.warning` | attachments/logs filling the volume | grow volume or archive; confirm backups are not stored on the same disk |

## 8. Incident quick actions (no deploy needed)

* One tenant misbehaving → `PUT /integrations/tenants/{t}/ {"status":"suspended"}` (history retained, access cut immediately).
* A host key leaked → `manage.py integration_key_revoke --kid <kid> --reason <text>`; if unsure of the extent
  `manage.py integration_set_active --slug <slug> --disable`.
* Rate-limit a noisy integration → lower `INTEGRATION_API_THROTTLE_RATE` and restart.
* Full procedures and the pilot checklist: [`runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md`](runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md).
