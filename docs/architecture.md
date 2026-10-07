# Architecture (overview)

* **Multi-tenancy.** Platform → Workspace (tenant) → Project (widget deployment). Isolation is enforced on every query and every socket by
  exact workspace/platform membership; tenants of a host application are reached only through explicit integration mappings.
* **Realtime.** Django Channels over Redis; browsers authenticate WebSockets with single-use, short-lived tickets
  ([`WEBSOCKET_PROTOCOL.md`](WEBSOCKET_PROTOCOL.md)); authorisation is re-checked live.
* **Integration.** Host applications connect through the versioned Integration Contract
  ([`integrations/INTEGRATION_CONTRACT_V1.md`](integrations/INTEGRATION_CONTRACT_V1.md)); design and decisions:
  [`architecture/INTEGRATION_PLATFORM.md`](architecture/INTEGRATION_PLATFORM.md).
* **Security.** [`SECURITY.md`](SECURITY.md). **Operations.** [`OPERATIONS.md`](OPERATIONS.md). **Deployment.** [`DEPLOYMENT.md`](DEPLOYMENT.md).
* Feature references: [`architecture/RICH_CHAT_ARCHITECTURE.md`](architecture/RICH_CHAT_ARCHITECTURE.md),
  [`architecture/AUTOMATION_ENGINE_REFERENCE.md`](architecture/AUTOMATION_ENGINE_REFERENCE.md),
  [`architecture/KNOWLEDGE_BASE_AND_MACROS_REFERENCE.md`](architecture/KNOWLEDGE_BASE_AND_MACROS_REFERENCE.md),
  [`architecture/COMMERCE_INTEGRATION.md`](architecture/COMMERCE_INTEGRATION.md).
