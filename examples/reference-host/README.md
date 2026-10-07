# Reference host: "Acme Learn" (not RastiSi)

A ~150-line, zero-dependency Node app that integrates RastiChat **using only Integration Contract v1**. It exists to prove
the reuse claim: *an unrelated product can integrate without any change to RastiChat core*. It has its own users,
organisations and login; RastiChat knows nothing about them except what the contract carries.

| Host concept | RastiChat concept | How |
|---|---|---|
| organisation `org-a`/`org-b`/`org-c` | tenant → workspace + project | `PUT /integrations/tenants/{id}` (`provision.mjs`) |
| alice / dave (students) | customers (verified visitors) | `/chat/identity` signs a customer assertion from the host's own session |
| bob (teacher) | staff `admin` of `org-a` | `PUT …/members/bob`; `/staff/chat` → dashboard SSO (assertion in the URL fragment) |
| per-org launcher / questions | project widget configuration | `defaults.widget` at provisioning (icon-only+no questions; one question; structured form) |

## Run it (local, synthetic data)
```bash
# 1. RastiChat operator: register the integration (host keeps its private key)
openssl genpkey -algorithm ed25519 -out host.private.pem && openssl pkey -in host.private.pem -pubout -out host.public.pem
python manage.py integration_create --slug acme-learn --name "Acme Learn" --platform-external-id <platform> \
  --scopes tenants:read,tenants:write,identity:customer,identity:staff
python manage.py integration_key_add --integration acme-learn --public-key-file host.public.pem      # prints kid=ick_…
# 2. host: provision tenants, run the app
export KEY_ID=ick_… INTEGRATION_SLUG=acme-learn RASTICHAT_URL=http://localhost:8080
node provision.mjs && node server.mjs      # http://localhost:4000/o/org-a
```
Automated proof: `integrations/tests_e2e.py` (backend, runs in CI) and `e2e/reference-host/` (Playwright, real browser).
