# Integration platform — rollout and rollback runbook

Scope: shipping a RastiChat release that contains the Integration Platform and enabling the **first host application** for a pilot tenant.
Host-neutral: `<slug>` is your integration's slug, `<tenant>` its external tenant id.
**This runbook is a plan for an operator.** Nothing in this repository executes it; every step that touches a production host needs the operator's
explicit go-ahead.

Related: [`DEPLOYMENT_ROLLBACK.md`](DEPLOYMENT_ROLLBACK.md) (generic deploy/rollback), [`INTEGRATION_PROVISIONING_SECURITY.md`](INTEGRATION_PROVISIONING_SECURITY.md)
(keys, rotation, emergencies), [`../UPGRADE.md`](../UPGRADE.md), [`../OPERATIONS.md`](../OPERATIONS.md), [`../integrations/INTEGRATION_CONTRACT_V1.md`](../integrations/INTEGRATION_CONTRACT_V1.md).

## 0. Principles

* The platform is additive. With **no `Integration` rows** the integration endpoints answer `401`/`404` and the legacy widget path is unchanged
  (`start_mode` defaults to `on_load` for projects that never had a widget configuration).
* The host adapter is **off by default** on the host's side: it needs a global switch *and* a per-tenant enablement. Deploying code changes nobody's behaviour.
* Deploy order is always: **RastiChat first, host adapter second, per-tenant enablement last.** Rollback is the reverse.
* Roll back **code, not schema**: previous images run on the migrated database (see §3).

## 1. Pre-flight

1. Back up PostgreSQL and `MEDIA_ROOT` ([`BACKUP_RESTORE.md`](BACKUP_RESTORE.md)); record the image tags / commit SHAs of RastiChat and of the host.
2. Confirm CI is green on the exact commit being deployed (`backend`, `nginx-config`, `widget`, `operator-dashboard`, `platform-dashboard`, `docker-build`,
   `secret-scan`, `e2e-reference-host`) and attach the release verification report to the release notes.
3. Review the migrations that will run — all additive, none rewrites data:

   | App | Migration | Effect |
   |---|---|---|
   | `integrations` | `0001`–`0004` | new tables only: `Integration`, keys, tenant mappings, external identities/memberships, idempotency records, host context |
   | `projects` | `0004_projectwidgetconfig` | new table `ProjectWidgetConfig` |
   | `conversations` | `0007_prechatsubmission` | new table |
   | `conversations` | `0008_support_threads` | adds `opened_by_side`, `subject_key` (**with database defaults**) and the partial unique index `uniq_active_support_thread` |
   | `notifications` | `0003_support_threads` | choices only (`SUPPORT_MESSAGE`) |

   On a populated database check the unique index can be created (must return no rows):
   `SELECT workspace_id, subject_key, count(*) FROM conversations_conversation WHERE type='PLATFORM_SUPPORT' AND subject_key <> '' AND status IN ('OPEN','PENDING','WAITING_FOR_WORKSPACE','WAITING_FOR_PLATFORM') GROUP BY 1,2 HAVING count(*) > 1;`
4. The **host generates its Ed25519 keypair on its own side** and gives RastiChat only the public key. (`integration_keygen` is for development
   and reference hosts; never create production host keys on the RastiChat server.)
5. Rehearse on a copy of production data if you can: `scripts/staging/sandbox/upgrade-migration.sh` does fresh + upgrade-with-data + reverse.

## 2. Rollout

### Step 1 — deploy RastiChat (no behaviour change)
* Build and deploy backend, widget bundle, operator and platform dashboards ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §6).
* `python manage.py migrate --noinput` **once**, before the new workers start.
* Verify: `GET /api/v1/health/ready/` all components up; the existing widget still starts a chat; operator login works; platform support inbox loads.
* Verify: `GET /api/v1/integrations/me/` without credentials → `401`.

### Step 2 — register the integration
```
python manage.py integration_create --slug <slug> --name "<Host name>" --platform-external-id <platform-external-id> \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,identity:platform,context:write,conversations:initiate
python manage.py integration_key_add --integration <slug> --public-key-file <host.public.pem>      # prints kid=ick_…
```
Grant only the scopes the host really uses. Verify with a signed `GET /api/v1/integrations/me/` from the host (staging first).

### Step 3 — deploy the host adapter (still dark)
Host-specific. Typical settings: global enable flag **off**, RastiChat base/WS/widget/dashboard URLs, the private key via the host's secret store, the `kid`,
the integration slug. The host should validate its settings at boot and fail fast. With no tenant enabled, the host's pages must render **no** chat
markup — verify with a page diff against the previous release.

### Step 4 — enable one pilot tenant
* Enable chat for one tenant in the host (its adapter provisions the tenant with `PUT /integrations/tenants/<tenant>/`; nothing is enabled if RastiChat
  is unreachable).
* Verify: the tenant exists in RastiChat; the launcher shows on that tenant's site only; its staff reach the inbox by SSO for that tenant only; other
  tenants are unchanged.
* Run the pilot checklist (§4).

### Step 5 — widen
One tenant at a time, watching the signals in §5 for at least one business day between increments.

## 3. Rollback (fastest first)

| Symptom | Action | Blast radius |
|---|---|---|
| One tenant misbehaving | disable chat for that tenant in the host, or `PUT /integrations/tenants/<tenant>/ {"status":"suspended"}`. Widget disappears, workspace inactive, sessions revoked, **history retained** | one tenant |
| Host adapter problem across tenants | turn the host's global switch off and restart it. No chat markup, hooks become no-ops | host-wide, no data loss |
| Integration compromised / key leak | `integration_key_revoke --kid <kid> --reason <text>`; if the extent is unclear `integration_set_active --slug <slug> --disable`. Host tokens stop verifying on the next request. Rotate the host key and add the new `kid` | one integration |
| RastiChat release defect | Redeploy the **previous** images and **leave the migrated schema in place**. New tables are ignored by the previous code, and the only new columns on existing tables (`conversations_conversation.opened_by_side`, `subject_key`) carry database-level defaults, so the previous release can still insert rows. This is verified, not assumed: `conversations.tests_support_threads.RollbackCompatibilityTests` and the staging rollback rehearsal (`scripts/staging/sandbox/rollback.sh`: snapshot → previous release on the migrated DB → customer flow → roll forward). Reversing migrations is also tested (`upgrade-migration.sh`) but loses data written to the new columns/tables — only do it deliberately. **Rule for future migrations:** a new NOT NULL column on an existing table needs `db_default`. | RastiChat |

Notes:
* Disabling never deletes conversations, visitors or memberships. Re-enabling restores them (provisioning is idempotent).
* A reverse migration of `conversations.0008` drops the unique index and the two columns; platform↔tenant threads would lose their thread identity.
  Prefer feature-off over migration rollback.
* After a **key** revoke, browser sessions already created through the host keep working until their normal expiry unless the integration is
  deactivated (deactivation revokes visitor sessions). Staff dashboard sessions from SSO last at most `INTEGRATION_STAFF_SESSION_MINUTES` (30) and cannot be refreshed.

## 4. Pilot verification checklist (per tenant)

- [ ] Anonymous visitor: small icon only, no form unless configured; chat opens, message arrives in the operator inbox.
- [ ] Signed-in customer: no second login; conversation shows the verified badge and is the same thread after re-login.
- [ ] Tenant staff (owner/admin/operator): the inbox opens from the host without a login screen and shows only this tenant.
- [ ] An `operator`-role member cannot change widget settings or members.
- [ ] Staff of tenant A cannot open tenant B (`403` / tenant mismatch).
- [ ] Tenant admin → platform support: the message shows up in the platform inbox and the reply arrives live.
- [ ] Platform owner → tenant: the message shows up in the tenant's support tab and as a notification.
- [ ] Removing a staff membership on the host removes inbox access immediately (REST) and closes live sockets within the revalidation interval; the next SSO is refused.
- [ ] Suspending the tenant removes the launcher and blocks new sessions; history stays visible to the platform.
- [ ] No token/assertion in any access-log line, URL query string or `Referer`: `grep -E 'assertion=|ticket=|session_token=|sig=' /var/log/nginx/rastichat-*.log` returns nothing.

## 5. What to watch

* The monitoring endpoint's `events_last_24h` counters ([`../OPERATIONS.md`](../OPERATIONS.md) §2): `token_refused:<code>` (a spike of `replay`/`invalid_token` = attack or clock
  skew), `scope_denied`, `cross_tenant_denied:*`, `rate_limited`, `ws_auth:refused`; and the log line `integration_replay_store_unavailable`
  (**Redis down → identity/API fail closed with 503**; guest widget chat over REST is unaffected).
* Throttle hits on `integration_api`, `identity_exchange`, `widget_config`.
* Host side: chat hooks must be best-effort and never block the host's own actions; the host re-runs its reconcile/provisioning to repair drift.
* Clock skew between hosts: tokens have 5 s leeway; keep NTP healthy.

## 6. Known limitations at this release

* **Event/webhook delivery from RastiChat to the host is not implemented** (designed in Contract §13, deliberately deferred — see
  [`../integrations/V1_SCOPE_AND_DEFERRALS.md`](../integrations/V1_SCOPE_AND_DEFERRALS.md)). Hosts push to RastiChat; they are not called back.
* Conversations still assigned to a removed member keep that assignee until an admin reassigns them.
* Metrics are structured log lines plus JSON counters on the monitoring endpoint; there is no Prometheus exporter.
* CI runs the generic reference-host browser E2E (`e2e-reference-host`). Browser tests against a *specific* host adapter and the full
  isolated-staging matrix (nginx TLS, multiple workers, soak, rollback rehearsal) run on the maintainers' sandbox, not in GitHub Actions.
