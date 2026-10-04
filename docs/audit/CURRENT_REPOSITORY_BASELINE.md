# Current Repository Baseline

- **Backend:** Django 4.2, Channels 4.0, Daphne (Port 8080)
- **Database:** PostgreSQL 15 (Port 5433)
- **Redis:** 7-alpine (Port 6380)
- **Frontend:** Next.js (Port 3000)
- **Widget:** Vite IIFE (Port 8081)

## Customer Message Flow
1. Visitor requests `/api/v1/widget/init/` with project key.
2. Server returns `session_token`.
3. Visitor calls `/api/v1/widget/start/` to get `conv_id`.
4. Visitor connects WS to `/ws/widget/{session_token}/{conv_id}/`.
5. Operator connects WS to `/ws/dashboard/{jwt}/{conv_id}/`.
6. Messages broadcast via Redis Channel Layer group `chat_{conv_id}`.

## Security Status
- Visitor identity is strictly bound to `session_token`.
- Operator identity strictly derived from JWT.
- `sender_id` from client payload is ignored.
- Cross-tenant access results in 404 (REST) or WS Close (Channels).