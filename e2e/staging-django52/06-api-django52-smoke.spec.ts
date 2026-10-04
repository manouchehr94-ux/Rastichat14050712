import { test, expect } from '@playwright/test';
import { BACKEND_URL, OPERATOR_EMAIL, OPERATOR_PASSWORD } from './env';

// Pure HTTP checks of behaviours that changed with Django 5.2 / DRF 3.17 / the P0-P1 hardening.
test.describe('API smoke on the Django 5.2 stack', () => {
  test('health: liveness and readiness are green; monitoring rejects anonymous callers', async ({ request }) => {
    expect((await request.get(`${BACKEND_URL}/api/v1/health/live/`)).status()).toBe(200);
    const ready = await request.get(`${BACKEND_URL}/api/v1/health/ready/`);
    expect(ready.status()).toBe(200);
    expect((await request.get(`${BACKEND_URL}/api/v1/health/monitoring/`)).status()).toBe(401);
  });

  test('JWT login works, bad password is 401, and the response carries the security headers', async ({ request }) => {
    const bad = await request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email: OPERATOR_EMAIL, password: 'definitely-wrong' } });
    expect(bad.status()).toBe(401);
    const ok = await request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email: OPERATOR_EMAIL, password: OPERATOR_PASSWORD } });
    expect(ok.status()).toBe(200);
    const body = await ok.json();
    expect(body.access).toBeTruthy();
    const h = ok.headers();
    expect(h['x-content-type-options']).toBe('nosniff');
    expect(h['strict-transport-security']).toBeTruthy();
    expect(h['content-security-policy']).toBeTruthy();
    expect(h['x-request-id']).toBeTruthy();
  });

  test('Django admin and the OpenAPI schema render under Django 5.2 / drf-spectacular 0.30', async ({ request }) => {
    const admin = await request.get(`${BACKEND_URL}/${process.env.DJANGO52_ADMIN_PATH || 'admin/'}login/`);
    expect([200, 404]).toContain(admin.status()); // 404 is fine when ADMIN_URL was moved off /admin/
    const schema = await request.get(`${BACKEND_URL}/api/schema/`);
    expect(schema.status()).toBe(200);
    expect((await schema.text()).length).toBeGreaterThan(1000);
  });

  test('P0 regressions hold on the new stack: no generic PATCH/DELETE on support or customer conversations', async ({ request }) => {
    const login = await (await request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email: OPERATOR_EMAIL, password: OPERATOR_PASSWORD } })).json();
    const auth = { Authorization: `Bearer ${login.access}` };
    const fake = '00000000-0000-0000-0000-000000000000';
    for (const method of ['patch', 'put', 'delete'] as const) {
      for (const base of ['support', 'platform/support']) {
        const r = await request[method](`${BACKEND_URL}/api/v1/${base}/${fake}/`, { headers: auth, data: {} });
        expect([403, 404, 405], `${method} ${base}`).toContain(r.status());
      }
      const c = await request[method](`${BACKEND_URL}/api/v1/conversations/customer/${fake}/`, { headers: auth, data: {} });
      expect([404, 405], `${method} customer`).toContain(c.status());
    }
  });

  test('ticket endpoint: unauthenticated is refused, a foreign conversation is a uniform 404', async ({ request }) => {
    const anon = await request.post(`${BACKEND_URL}/api/v1/ws/ticket/`, { data: { kind: 'notifications' } });
    expect([401, 403]).toContain(anon.status());
    const login = await (await request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email: OPERATOR_EMAIL, password: OPERATOR_PASSWORD } })).json();
    const r = await request.post(`${BACKEND_URL}/api/v1/ws/ticket/`, {
      headers: { Authorization: `Bearer ${login.access}` }, data: { kind: 'dashboard_chat', conversation_id: '00000000-0000-0000-0000-000000000000' },
    });
    expect(r.status()).toBe(404);
  });
});
