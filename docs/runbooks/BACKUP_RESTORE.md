# Backup and Restore

## Backup

```bash
scripts/staging/backup.sh .env.staging
```

Produces, in `$BACKUP_DIR` (default `/opt/rastichat/backups`):

- `rastichat-db-<timestamp>.sql.gz` (+ `.sha256`) — `pg_dump | gzip`.
- `rastichat-media-<timestamp>.tar.gz` (+ `.sha256`) — the media directory.
- `rastichat-meta-<timestamp>.json` — timestamp, database name, **both**
  archives' filenames, checksums, and sizes (`db_backup_sha256`/
  `db_backup_size_bytes`, `media_backup_sha256`/`media_backup_size_bytes` —
  the latter three are `null` if there was no media directory yet to back
  up), image tag, git SHA. **Never contains secrets** — the `.env`
  file itself is deliberately not copied into the backup.

Refuses to overwrite an existing file of the same name (timestamped to
the second — this should only ever collide if run twice in the same
second). Applies retention (`BACKUP_RETENTION_DAYS`, default 14) at the
end of every run.

Run on a schedule (cron/systemd timer), e.g. daily at 03:00:

```
0 3 * * * cd /opt/rastichat/app && scripts/staging/backup.sh .env.staging >> /var/log/rastichat-backup.log 2>&1
```

## Verify a backup (no database touched)

```bash
scripts/staging/verify-backup.sh /opt/rastichat/backups/rastichat-db-<timestamp>.sql.gz
```

Checks the sha256 checksum and the gzip/tar stream integrity — and, given
a DB backup file, also finds that timestamp's `rastichat-meta-*.json` and,
if it names a media backup, verifies that archive too (one backup "set" is
one thing to trust, not two files to remember to check separately). Run
this before trusting any backup you're about to restore from, especially
one copied off the VPS first.

## Restore

**Into a disposable copy (safe — always do this first to prove a backup actually restores):**

```bash
scripts/staging/restore.sh /opt/rastichat/backups/rastichat-db-<timestamp>.sql.gz --target-db=rastichat_restore_test
```

Drops/recreates `rastichat_restore_test` fresh and loads the backup into
it. The live database is never touched. Inspect it, then drop it:

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging exec db \
  psql -U rastichat -d postgres -c 'DROP DATABASE rastichat_restore_test;'
```

**Into the live database (destructive — requires `--yes`):**

```bash
scripts/staging/restore.sh /opt/rastichat/backups/rastichat-db-<timestamp>.sql.gz --yes
```

This drops and recreates the real database and loads the backup —
everything written since that backup was taken is gone. Take a fresh
backup of the current (pre-restore) state first if there's any chance
you'll want it:

```bash
scripts/staging/backup.sh .env.staging
```

## Media restore

The `rastichat-media-<timestamp>.tar.gz` produced by `backup.sh` is a
plain tarball of `MEDIA_HOST_PATH`. To restore it:

```bash
sudo tar -xzf /opt/rastichat/backups/rastichat-media-<timestamp>.tar.gz -C /opt/rastichat/
# extracts to /opt/rastichat/media/ (matches MEDIA_HOST_PATH's basename) — back up the
# current directory first if you want to keep it:
sudo mv /opt/rastichat/media /opt/rastichat/media.bak-$(date +%s)
```

## This has actually been tested

The restore mechanism (`pg_dump | gzip` -> checksum -> verify -> drop/recreate
-> restore -> confirm data matches) was run for real against a live
Postgres database during development of these scripts — not just written
and assumed to work. The `docker compose exec db ...` wrapping the real
scripts use could not be exercised in that same test (no built Docker
image was available in that sandbox); the SQL/backup/restore logic
itself was proven end-to-end. Re-verify on your actual staging Postgres
the first time you use this in anger:

```bash
scripts/staging/backup.sh .env.staging
scripts/staging/restore.sh /opt/rastichat/backups/rastichat-db-<the file just created>.sql.gz --target-db=rastichat_restore_smoketest
docker compose -f docker-compose.staging.yml --env-file .env.staging exec db \
  psql -U rastichat -d rastichat_restore_smoketest -c '\dt'   # confirm tables exist
docker compose -f docker-compose.staging.yml --env-file .env.staging exec db \
  psql -U rastichat -d postgres -c 'DROP DATABASE rastichat_restore_smoketest;'
```
