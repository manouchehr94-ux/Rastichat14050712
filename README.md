# RastiChat - Phase 1

RastiChat is a multi-tenant chat platform connecting customers with store admins, and store admins with platform support.

## Technology Stack
- Backend: Django, DRF, Channels, PostgreSQL, Redis
- Frontend: Next.js, TypeScript, Tailwind
- Widget: Vite, TypeScript
- Infra: Docker Compose

## Local Setup
1. `docker-compose up -d --build`
2. `docker-compose exec backend python manage.py migrate`
3. `docker-compose exec backend python seed_data.py`
4. Build widget: `cd packages/widget && npm install && npm run build && cd ..\..`
5. Run operator dashboard: `cd apps/operator-dashboard && npm run dev`
6. Run platform dashboard: `cd apps/platform-dashboard && npm run dev`

## Demo Accounts
- Operator: operator@ws.com / pass1234
- Platform Support: support@platform.com / pass1234
