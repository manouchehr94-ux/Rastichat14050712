# Integration platform — rollout and rollback runbook

Scope: shipping the generic Integration Platform (PRs A–E) and enabling the
first host adapter (RastiSi) for pilot stores. **This runbook is a plan.
Nothing in it has been executed against production.** Every step that touches
a production host needs the operator's explicit go-ahead.

Related: `docs/runbooks/DEPLOYMENT_ROLLBACK.md` (generic deploy/rollback),
`docs/runbooks/INTEGRATION_PROVISIONING_SECURITY.md`,
`docs/integrations/INTEGRATION_CONTRACT_V1.md`.

## 0. Principles

* The platform is additive. With **no `Integration` rows** the new endpoints
  return `401/404` and the legacy widget path is unchanged
  (`start_mode` defaults to `on_load` for existing projects).
* The host adapter is **off by default**: the RastiSi side requires
  `RASTICHAT_INTEGRATION_ENABLED=1` globally *and* a per-store enablement. Rolling out the
  code changes no store's behaviour.
* Deploy order is always: RastiChat first, host adapter second, per-store
  enablement last. Rollback is the reverse.

## 1. Pre-flight (before any deploy)

1. Take a database backup (`docs/runbooks/BACKUP_RESTORE.md`) and record the
   current image tags / commit SHAs of RastiChat and RastiSi.
2. Confirm CI is green on the exact commit being deployed and the combined
   verification branch result is attached to the release notes.
3. Review migrations that will run (all additive; no data rewrites):

   | App | Migration | Effect |
   |---|---|---|
   | integrations | 0001–0003 | new tables only (`Integration`, keys, mappings, identities, memberships, idempotency) |
   | projects | widget config | new table `ProjectWidgetConfig` |
   | conversations | 0008 | adds `opened_by_side`, `subject_key` + partial unique index `uniq_active_support_thread` |
   | notifications | 0003 | choices only (`SUPPORT_MESSAGE`) |

   Check the partial unique index can be created: on a populated database run
   `SELECT workspace_id, subject_key, count(*) FROM conversations_conversation
   WHERE subject_key <> '' AND status IN (…active…) GROUP BY 1,2 HAVING count(*)>1;`
   It must return no rows (legacy rows have an empty `subject_key`).
4. Generate the host's Ed25519 key **on the host side**
   (`python manage.py integration_keygen --out-dir <dir>` writes a keypair; keep
   the private half in the host's secret store, never in the repository). Only the public key is given to RastiChat.

## 2. Rollout

### Step 1 — deploy RastiChat (no behaviour change)
* Deploy backend, widget bundle, operator and platform dashboards.
* `python manage.py migrate` (additive).
* Verify: `/healthz`, existing widget still starts a chat, operator login
  works, platform support inbox loads.
* Verify: `GET /api/v1/integrations/me/` without credentials → `401`.

### Step 2 — register the integration
```
python manage.py integration_create --slug rastisi --name RastiSi \
    --platform-external-id <platform-external-id> \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,identity:platform,conversations:initiate
python manage.py integration_key_add --integration rastisi --public-key-file <pub.pem>
```
* Verify with a signed `GET /integrations/me/` from the host (staging first).

### Step 3 — deploy the RastiSi adapter (still dark)
* Set env on the host: `RASTICHAT_INTEGRATION_ENABLED=1`, `RASTICHAT_BASE_URL`,
  `RASTICHAT_WS_BASE` (optional), `RASTICHAT_WIDGET_URL`,
  `RASTICHAT_DASHBOARD_URL`, `RASTICHAT_PLATFORM_DASHBOARD_URL`,
  `RASTICHAT_PRIVATE_KEY` or `RASTICHAT_PRIVATE_KEY_FILE`, `RASTICHAT_KEY_ID`
  (the `kid` printed by `integration_key_add`), `RASTICHAT_INTEGRATION_SLUG`. Settings are validated at boot and fail fast.
* With no store enabled, storefront pages render **no** chat markup. Verify
  with a page diff against the previous release.

### Step 4 — enable one pilot store
* Platform admin → store detail → *Chat* tab → enable. (Alternatively
  `chat_sync_tenants`.)
* Verify: tenant visible in RastiChat admin, widget launcher on that store's
  storefront only, merchant "Customer chat" / "Platform support" tabs appear
  for that store only, other stores unchanged.
* Run the manual checklist in §4.

### Step 5 — widen
One store at a time, observing §5 signals for at least one business day
between increments.

## 3. Rollback (fastest first)

| Symptom | Action | Blast radius |
|---|---|---|
| One store misbehaving | Platform admin → store → *Chat* → disable (or suspend tenant via API). Widget disappears, workspace is set inactive, **history retained**. | one store |
| Adapter problem across stores | Set `RASTICHAT_INTEGRATION_ENABLED=0` on the host and restart. No chat markup rendered, hooks become no-ops. | host-wide, no data loss |
| Integration compromised / key leak | `integration_key_revoke --kid <kid> --reason <text>`; or `integration_set_active --slug rastisi --disable`. All host tokens stop verifying immediately (keys are looked up on every request). Rotate the host key, add the new kid. | integration |
| RastiChat release defect | Redeploy the previous RastiChat image. Migrations are additive and the previous code ignores the new tables/columns; **do not reverse migrations** unless data loss is acceptable. | RastiChat |

Notes:
* Disabling never deletes conversations, visitors or memberships. Re-enabling
  restores them (provisioning is idempotent).
* A reverse migration of `conversations.0008` drops the unique index and the
  two columns; platform↔tenant threads would then lose their thread identity.
  Prefer feature-off over migration rollback.
* After a key revoke, existing browser sessions created through the host keep
  working until their normal expiry unless the integration is deactivated
  (deactivation revokes visitor sessions).

## 4. Pilot verification checklist (per store)

- [ ] Anonymous visitor: small icon only, no form unless configured; chat opens, message arrives in the operator inbox.
- [ ] Logged-in customer: no second login; conversation shows the verified badge and is the same thread after re-login.
- [ ] Merchant owner/admin/order-manager: *Customer chat* opens the operator dashboard without a login screen, sees only this store.
- [ ] Order-manager role is `operator` (cannot change widget settings or members).
- [ ] Staff of store A cannot open store B (`403` / tenant mismatch).
- [ ] Merchant → platform support: message shows up in the platform inbox, reply arrives live.
- [ ] Platform owner → store: message shows up in the merchant *Platform support* tab and as a notification.
- [ ] Revoking a store membership on the host removes chat access within the staff-token TTL (≤ 120 s) and on next SSO.
- [ ] Disabling the store removes the launcher and blocks new sessions; history still visible to the platform owner.
- [ ] No token/assertion in any access log line, URL query string or Referer (`grep` the proxy logs for `assertion=` / `token=`).

## 5. What to watch

* RastiChat logs (structured): `integration_token_refused` by `code`
  (a spike of `replay`/`bad_signature` = attack or clock skew),
  `integration_scope_denied`, `replay_store_unavailable`
  (**Redis down → identity/API fail closed with 503**; widget guest chat is unaffected).
* Throttle hits on `integration_api`, `identity_exchange`, `widget_config`.
* Host side: `chat_integration` hook failures are logged and never block the
  store action; re-run `chat_sync_tenants` to reconcile.
* Clock skew between hosts: tokens have a 5 s leeway; keep NTP healthy.

## 6. Known limitations at this release

* Event/webhook delivery from RastiChat to the host is specified in the
  contract but **not implemented**; the host polls or relies on the browser.
* Browser E2E suites run locally via their `run.sh`; they are not yet wired
  into GitHub Actions.
* Metrics are structured log lines only (no Prometheus counters yet).
