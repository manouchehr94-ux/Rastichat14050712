# RastiSi as a RastiChat host (first adapter)

RastiSi is **one host of the generic Integration Contract v1**, nothing more. RastiChat contains no RastiSi-specific
code; everything RastiSi-specific lives in RastiSi's `apps/chat_integration` (see `docs/integrations/RASTICHAT.md` in the
RastiSi repository). Any other product integrates the same way: `docs/integrations/INTEGRATE_NEW_APPLICATION.md`.

## What RastiSi uses

| Need | Contract feature |
|---|---|
| a chat per store | tenant provisioning (`PUT /integrations/tenants/{store public id}/`), `verified_domains` = the store's verified storefront hosts |
| customer on a storefront, no second login | `customer` identity assertion → `/identity/customer/` (guest allowed; upgrade on login) |
| merchant staff in the inbox, no second login | `tenant_staff` assertion → `/identity/staff/`; roles owner/admin/operator; **one staff `sub` per (person, store)** |
| merchant ⇄ RastiSi team | platform↔tenant support threads (§12): merchant opens from the operator dashboard, platform replies in the platform dashboard |
| RastiSi team → store (store never wrote first) | `POST /integrations/tenants/{t}/support-conversations/` with scope `conversations:initiate`, an `Idempotency-Key`, initiated by a platform owner |
| store suspended / membership revoked / user suspended | tenant status, `members/{u}` DELETE, `users/{u}/disable/` |

Integration registration (staging/production is an operator action, see the rollout runbook):

```
integration_create --slug rastisi --name RastiSi --platform-external-id <id> \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,identity:platform,conversations:initiate
```

## Safety properties (verified)

* **Off by default.** `RASTICHAT_INTEGRATION_ENABLED=0` ships nothing; a store only gets chat when a RastiSi *platform
  superuser* enables it for that store. Merchants cannot enable it themselves.
* The tenant id never comes from the browser (storefront Host / admin Host + ACTIVE membership / superuser-only URL).
* Credentials only travel in a URL **fragment** (never a query string) and are single-use; API tokens are bound to
  method + URL + body hash and live 30 s.
* A person who runs several stores has an isolated RastiChat account in each.

## Verification

* RastiSi: `apps.chat_integration` tests (adapter, gating, hooks, roles, isolation).
* Cross-system, real Chromium, both systems running locally with synthetic data:

  ```
  RASTISI_DIR=/path/to/rastisi E2E_PYTHON=<rastichat venv python> SI_PYTHON=<rastisi venv python> e2e/rastisi/run.sh
  ```

  Scenarios (8): default-off; platform enables pilot stores only; guest customer → store admin (SSO) → live reply;
  signed-in customer (verified badge) and a multi-store owner not seeing another store's chats; foreign-store member and
  order-manager refusals; merchant → platform support thread and live reply; platform → store first message; membership
  revocation and per-store disable.
* The runner creates throw-away databases (`rc_e2e_si`, `rs_e2e`) and only talks to `localhost` / `*.rastisi.localhost`.
