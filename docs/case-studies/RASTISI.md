# Case study: an e-commerce platform as a host (RastiSi)

> **Optional reading.** RastiChat has no dependency on this page or on the product it describes. It is kept as a worked example of
> a *multi-tenant commerce platform* using only Integration Contract v1. A new project should start from
> [`../integrations/NEW_PROJECT_GUIDE.md`](../integrations/NEW_PROJECT_GUIDE.md) and the generic
> [reference host](../../examples/reference-host/).

RastiSi is **one host of the generic Integration Contract v1** — the first one, and one of several possible. RastiChat contains no
RastiSi-specific code; everything RastiSi-specific lives in RastiSi's own repository (an adapter app, `apps/chat_integration`). The adapter
gets no privileged path: it uses exactly the documented contract.

## What the host uses

| Host need | Contract feature |
|---|---|
| a chat per store | tenant provisioning (`PUT /integrations/tenants/{store public id}/`), `verified_domains` = the store's verified storefront hosts |
| a customer on a storefront, no second login | `customer` identity assertion → `/identity/customer/` (guest allowed; upgrade on login) |
| a merchant's staff in the inbox, no second login | `tenant_staff` assertion → `/identity/staff/`; roles owner/admin/operator; **one staff `sub` per (person, store)** |
| merchant ⇄ platform team | platform↔tenant support threads (Contract §12): the merchant opens from the operator dashboard, the platform replies in the platform dashboard |
| platform team → store that never wrote first | `POST /integrations/tenants/{t}/support-conversations/` with scope `conversations:initiate`, an `Idempotency-Key`, initiated by a platform owner |
| store suspended / membership revoked / user suspended | tenant status, `members/{u}` DELETE, `users/{u}/disable/` |

Registration is an operator action of the RastiChat deployment (see [`../runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md`](../runbooks/INTEGRATION_ROLLOUT_ROLLBACK.md)):

```
integration_create --slug <host slug> --name "<Host name>" --platform-external-id <id> \
    --scopes tenants:read,tenants:write,identity:customer,identity:staff,identity:platform,conversations:initiate
```

## Safety properties demonstrated

* **Off by default.** The host ships the adapter disabled; a store gets chat only when the host's own platform operator enables it, and
  that enablement provisions the remote tenant first (nothing is enabled if RastiChat is unreachable).
* The tenant id never comes from the browser (it is derived from the host-side session/host name and active membership).
* Credentials travel only in a URL **fragment** (never a query string) and are single-use; API tokens are bound to method + URL + body hash
  and live 30 s.
* A person who runs several stores has an isolated RastiChat account per store (namespaced staff `sub`).

## How it was verified

* The adapter's own test-suite in the host repository.
* A cross-system browser test (real Chromium; the host and a RastiChat stack both running locally with synthetic data) with eight scenarios:
  default-off; operator enables pilot stores only; guest customer → store admin (SSO) → live reply; signed-in customer (verified badge) and a
  multi-store owner not seeing another store's chats; foreign-store member and order-manager refusals; merchant → platform support thread and
  live reply; platform → store first message; membership revocation and per-store disable. It needs a checkout of the host and lives with the
  maintainers' verification tooling, not in the generic CI.
* The generic reference host proves the same contract without any of this host's concepts: `e2e/reference-host/`.
