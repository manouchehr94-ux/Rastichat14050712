# Upgrading RastiChat

How to move a running deployment to a newer version safely, and how to back out. The deployment mechanics (images, compose, nginx) are in
[`DEPLOYMENT.md`](DEPLOYMENT.md); this page is about *compatibility* and *order*.

## 1. Before you start

1. **Read [`CHANGELOG.md`](../CHANGELOG.md)** for the versions you are skipping: it lists new migrations, new/changed environment variables
   and anything that needs operator action.
2. **Back up** PostgreSQL and `MEDIA_ROOT` and *verify* the backup restores ([`runbooks/BACKUP_RESTORE.md`](runbooks/BACKUP_RESTORE.md);
   `scripts/staging/backup.sh` / `verify-backup.sh`). Note the currently deployed image tags / git SHA — that is your rollback target.
3. **Rehearse** on a copy: restore last night's backup into a scratch database and run the new release's migrations against it.
   (`scripts/staging/sandbox/upgrade-migration.sh` automates "fresh database" *and* "upgrade from the previous release with seeded rows".)
4. Run the deploy gate with the **production** environment variables: `python manage.py check --deploy --fail-level WARNING --tag security`.
5. Compare your `.env` with the shipped `.env.production.example`; new variables always have safe defaults, but read the changelog. **Operator action:** staging/production now refuse to start without an explicit, non-default database password (`DB_PASSWORD`, or a password inside `DATABASE_URL`) — confirm yours is set *before* you deploy this release.

## 2. Order of operations

```
backup → build images → migrate (ONCE) → backend workers (rolling) → schedulers → dashboards + widget → smoke checks
```

* **Migrate exactly once per release**, as a separate job, *before* any new backend worker starts (`docker-entrypoint.sh migrate`, or
  `python manage.py migrate --noinput`). The `web` role never migrates by itself, so replicas cannot race each other's schema changes.
* Roll backend replicas **one at a time**. Old and new workers coexist briefly; that is safe because of the compatibility rule below.
* A worker that stops closes its WebSockets; clients reconnect (new single-use ticket + history resync) onto another worker. Expect a brief
  burst of `ws_auth` events and no lost messages (messages are in PostgreSQL; clients resync).
* Dashboards embed the API/WS base URLs at build time: rebuild them if those URLs change. The widget URL `/widget.js` is `no-cache,
  must-revalidate`, so sites using the unversioned URL pick up a new widget on their next page load; pinned `/widget/<version>/widget.js`
  URLs keep serving the old (immutable) bundle.
* Smoke checks: `GET /api/v1/health/ready/` (all components up), a login, a widget conversation, one operator reply
  ([`OPERATIONS.md`](OPERATIONS.md)).

## 3. Compatibility rules the project follows

* **Expand / contract migrations.** A release's migrations must leave the *previous* release working: new tables are additive, and a new
  `NOT NULL` column on an existing table **must have a database-level default** (`db_default`). This is what lets you roll the code back
  without reversing the schema. It is enforced by a regression test (`conversations.tests_support_threads.RollbackCompatibilityTests`
  checks the column defaults in `information_schema` and performs a raw old-style `INSERT`), and verified by the rollback rehearsal in the staging matrix.
  Destructive steps (dropping/renaming columns) are only ever done one release *after* the code stopped using them.
* **All migrations are reversible** and checked fresh-database and upgrade-with-data. Reversing loses data written to new columns/tables, so
  prefer rolling the *code* back.
* **API stability.** REST lives under `/api/v1/`; evolution is additive (new optional request fields, new response fields, new endpoints).
  Clients must ignore unknown response fields. The host-facing contract has its own version header (`X-RastiChat-Contract: integration-v1`);
  a breaking change would ship as `integration-v2` on new paths, with v1 supported for ≥ 12 months and `Deprecation`/`Sunset` headers.
  Existing widget/dashboard request and response shapes are not changed by additive releases; projects without widget configuration keep
  the original widget behaviour.
* **Widget bundles** pinned on third-party sites keep working against newer backends (the backend keeps accepting what older widgets send).

## 4. Rolling back

| Situation | Action |
|---|---|
| a new feature misbehaves for one tenant | suspend that tenant or disable the integration — no deploy ([`OPERATIONS.md`](OPERATIONS.md) §8) |
| the release is defective | redeploy the previous images **and leave the migrated schema in place** (`scripts/staging/rollback.sh <git sha>` retags and restarts) |
| a migration itself failed midway | do not re-run blindly: inspect, fix forward, or restore the pre-release backup |
| data corruption | restore the backup (`rollback.sh --restore-db=… --yes` is destructive and explicit) |

Never use `docker compose down -v` as a rollback step.

## 5. Migrations introduced by the integration platform (first release containing it)

| App | Migration | Effect |
|---|---|---|
| `integrations` | `0001`–`0004` | new tables only: integrations, keys, tenant mappings, external identities and memberships, idempotency records, host context |
| `projects` | `0004_projectwidgetconfig` | new table `ProjectWidgetConfig` |
| `conversations` | `0007_prechatsubmission` | new table for pre-chat answers |
| `conversations` | `0008_support_threads` | adds `opened_by_side` and `subject_key` **with `db_default`**, plus the partial unique index `uniq_active_support_thread` |
| `notifications` | `0003_support_threads` | choices only (`SUPPORT_MESSAGE`) |

Before `conversations.0008` on a populated database, confirm the unique index can be created — this must return no rows (legacy rows have an
empty `subject_key`):

```sql
SELECT workspace_id, subject_key, count(*) FROM conversations_conversation
WHERE type = 'PLATFORM_SUPPORT' AND subject_key <> '' AND status IN ('OPEN','PENDING','WAITING_FOR_WORKSPACE','WAITING_FOR_PLATFORM') GROUP BY 1,2 HAVING count(*) > 1;
```

(Legacy rows have `subject_key = ''`, so the query is expected to be empty.)

New environment variables in that release are all optional with safe defaults: `INTEGRATION_TOKEN_AUDIENCE`, `INTEGRATION_API_THROTTLE_RATE`,
`INTEGRATION_AUTH_FAILURES_PER_MINUTE`, `INTEGRATION_STAFF_SESSION_MINUTES`, `IDENTITY_EXCHANGE_THROTTLE_RATE`, `WIDGET_CONFIG_THROTTLE_RATE`,
`WS_REVALIDATE_INTERVAL_SECONDS` ([`runbooks/ENVIRONMENT_VARIABLES.md`](runbooks/ENVIRONMENT_VARIABLES.md)).

## 6. Dependency and runtime upgrades

Upgrade Django / Python / Node on a staging copy first and run the whole suite. The repository's own gates catch most regressions:
backend tests, `makemigrations --check`, `check --deploy`, Bandit, `pip-audit`, the widget/dashboard tests and builds, the nginx config
tests and the reference-host E2E ([`.github/workflows/pr-checks.yml`](../.github/workflows/pr-checks.yml)).
