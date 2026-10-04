# Incident Checklist

Quick triage for "something's wrong in staging/production." Work top to
bottom; stop as soon as you've found and fixed the cause.

## 1. Is it actually down?

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://chat-staging.rastisi.ir/api/v1/health/ready/
curl -fsS -o /dev/null -w '%{http_code}\n' https://operator-chat-staging.rastisi.ir/
curl -fsS -o /dev/null -w '%{http_code}\n' https://platform-chat-staging.rastisi.ir/
```

- All three fail / connection refused -> jump to **2. Host/Nginx**.
- Only the backend fails -> jump to **3. Backend/DB/Redis**.
- Backend is 200 but a specific feature is broken -> jump to **5. Feature-specific**.

## 2. Host / Nginx

```bash
sudo nginx -t
sudo systemctl status nginx
sudo ss -ltnp | grep -E ':80 |:443 '
```

- `nginx -t` fails -> a bad config was installed. Check
  `/etc/nginx/sites-available/rastichat-*.conf` against
  `deploy/nginx/sites/*.conf.template`; re-run
  `sudo scripts/nginx/install-sites.sh .env.staging` after fixing.
- Nginx isn't running -> `sudo systemctl start nginx`, then re-check `nginx -t` first.
- Certificate expired -> `sudo scripts/nginx/issue-certs.sh .env.staging`
  (also check `sudo certbot certificates` and that the renewal timer is active:
  `systemctl list-timers | grep certbot`).

## 3. Backend / DB / Redis

```bash
scripts/staging/status.sh .env.staging
docker compose -f docker-compose.staging.yml --env-file .env.staging ps
docker compose -f docker-compose.staging.yml --env-file .env.staging logs --tail 100 backend
```

- `db` container unhealthy -> `docker compose logs db`; check disk space
  (`df -h`) — Postgres refusing writes on a full disk is a common cause.
- `redis` container unhealthy -> `docker compose logs redis`; WebSockets
  and the WS rate limiter both depend on it.
- `backend` container restarting in a loop -> `docker compose logs backend`
  for the actual traceback; check recent deploys
  (`git log --oneline -5` on the VPS checkout) — consider
  `scripts/staging/rollback.sh <previous-git-sha>`.

## 4. Recent deploy?

```bash
scripts/staging/status.sh .env.staging   # shows the currently-deployed git SHA
git log --oneline -10
```

If the incident started right after a deploy, roll back first, ask
questions after:

```bash
scripts/staging/rollback.sh <previous-known-good-git-sha>
```

## 5. Feature-specific

| Symptom | Check |
|---|---|
| Widget doesn't load on the storefront | Storefront domain in `CORS_ALLOWED_ORIGINS`? Widget origin in the WS `OriginValidator` list (same setting, `config/asgi.py`)? Browser console for the actual error. |
| Messages don't arrive live (WSS) | `docker compose logs redis`; Nginx `location /ws/` proxy headers (`deploy/nginx/snippets/websocket-params.conf`) still in place after any manual Nginx edits? |
| Images/attachments 404 | `/media/` alias in the Nginx backend site pointing at the right `MEDIA_HOST_PATH`? Does that directory actually contain the file (`docker compose exec backend ls /app/media`)? |
| Automation/macro not firing | `GET /api/v1/health/monitoring/` — is `automation-worker` stale? `docker compose logs automation-worker`. |
| SLA states not updating | Same as above for `sla-worker`. |
| Login fails for everyone | Check `docker compose logs backend` for a DB connectivity error; confirm `DJANGO_SECRET_KEY` wasn't accidentally rotated (invalidates existing JWTs). |
| 429 Too Many Requests reported by real users | Check which scope in `docs/runbooks/ENVIRONMENT_VARIABLES.md` "Rate limiting" — is the configured rate actually too tight for real traffic? Adjust the corresponding `*_THROTTLE_RATE` env var and restart. |

## 6. After resolving

- Update `docs/runbooks/MONITORING_RUNBOOK.md` or this checklist if the
  cause wasn't already covered.
- If data was restored from backup, note which backup file and why in
  the deploy/incident log.
- Confirm the staging smoke suite passes before considering it closed:

```bash
cd e2e/staging-smoke && npx playwright test
```
