# Deployment and Rollback

## Deploy

```bash
scripts/staging/deploy.sh .env.staging
```

Steps, in order, stopping at the first failure:

1. **Validate** — `docker compose config --quiet` (exercises every
   required-secret/domain guard in `docker-compose.staging.yml`).
2. **Backup** — skipped gracefully if `db` isn't already running
   (first-ever deploy has nothing to back up yet).
3. **Build** — every image, tagged with both the moving `:$IMAGE_TAG`
   and the immutable current git SHA (so `rollback.sh` has something
   concrete to retag back to later).
4. **Migrate** — `docker compose run --rm backend migrate`, a one-off
   run BEFORE any `web` replica starts on the new image. The `web`
   entrypoint subcommand never runs migrations itself, specifically so
   a multi-replica rolling deploy can't race two replicas' migrations
   against each other.
5. **Deploy** — `docker compose up -d`.
6. **Health check** — polls `/api/v1/health/ready/` for up to 60s.
7. **Smoke test** — confirms backend/operator-dashboard/platform-dashboard/widget
   all respond (not necessarily 200 — anything under 500).

On success: prints the deployed git SHA and timestamp.

## Rollback

**Code/image rollback** (requires that SHA was previously built by
`deploy.sh` on this same host — images are local, not fetched from a
registry):

```bash
git log --oneline -10                      # find the commit to roll back to
scripts/staging/rollback.sh <git-sha>
```

Retags `rastichat-{backend,operator-dashboard,platform-dashboard,widget}:<git-sha>`
onto the moving `:$IMAGE_TAG` tag and restarts — no rebuild, no
checkout. Health-checks the same way `deploy.sh` does.

**Database rollback** (separate, explicit, destructive — only when the
code rollback alone isn't enough, e.g. a bad migration already ran):

```bash
scripts/staging/rollback.sh <git-sha> --restore-db=/opt/rastichat/backups/rastichat-db-<timestamp>.sql.gz --yes
```

## Migration rollback limitation

Django migrations are **not** automatically reversed by `rollback.sh`.
Rolling back code past a migration that isn't purely additive (renamed
or dropped a column the old code doesn't expect) requires either:

- a compatible `python manage.py migrate <app> <prior_migration>` run
  by hand first (only safe if that specific migration has a clean
  reverse — check it before running), or
- restoring the database backup taken **before** that migration ran
  (see `--restore-db` above).

There is no way to make this fully automatic in general — not every
migration has a safe, lossless reverse. This project's migrations to
date have all been additive (new apps/tables/columns), so this has not
yet been a real scenario, but a future non-additive migration would
need one of the two paths above.

## What rollback never does

`docker compose down -v` is never run by any of these scripts —
that destroys the named volumes (Postgres/Redis/media/static data),
which is never what a rollback should do. `rollback.sh` only retags
images and restarts containers; the `--restore-db` path is a separate,
explicit, opt-in step.

## If a deploy fails partway through

- **Failed at build**: nothing was touched yet — safe to fix and re-run
  `deploy.sh`.
- **Failed at migrate**: check `docker compose logs backend`. If the
  migration itself failed (not a connectivity issue), do not re-run
  `deploy.sh` blindly — inspect the migration, fix forward, or restore
  from the backup `deploy.sh` just took in step 2.
- **Failed at health check / smoke test**: the new containers are
  running but unhealthy. Run `scripts/staging/status.sh` to see why,
  then either fix forward and re-run `deploy.sh`, or
  `scripts/staging/rollback.sh <previous-git-sha>` to the last known-good
  commit.
