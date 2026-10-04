# Architecture

## Multi-Tenancy
- Platform -> Workspace -> Project hierarchy.
- Strict DB-level isolation using workspace memberships.

## Real-time
- Django Channels with Redis.
- WebSocket auth via JWT for operators and session tokens for visitors.

## Security
- RBAC enforced on all API endpoints and WebSocket consumers.
- Idempotency keys for message delivery.
