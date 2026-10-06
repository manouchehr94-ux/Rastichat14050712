// Minimal RastiChat Integration Contract v1 client — Node >= 18, ZERO dependencies (node:crypto has Ed25519).
// This is the whole "SDK" a host needs on the server side: sign short-lived EdDSA JWTs, call the API, mint assertions.
import crypto from 'node:crypto';
import { randomUUID } from 'node:crypto';

const b64url = (buf) => Buffer.from(buf).toString('base64url');

export function signJwt(claims, privateKeyPem, kid) {
  const header = b64url(JSON.stringify({ alg: 'EdDSA', typ: 'JWT', kid }));
  const payload = b64url(JSON.stringify(claims));
  const signature = crypto.sign(null, Buffer.from(`${header}.${payload}`), crypto.createPrivateKey(privateKeyPem));
  return `${header}.${payload}.${b64url(signature)}`;
}

export class RastiChatClient {
  /** @param {{baseUrl: string, slug: string, kid: string, privateKeyPem: string, audience?: string}} o */
  constructor({ baseUrl, slug, kid, privateKeyPem, audience = 'rastichat' }) {
    Object.assign(this, { baseUrl: baseUrl.replace(/\/$/, ''), slug, kid, privateKeyPem, audience });
  }

  /** Server-to-server call: a NEW token per request (also per retry), bound to method, path and body. */
  async request(method, path, body) {
    const raw = body === undefined ? '' : JSON.stringify(body);
    const now = Math.floor(Date.now() / 1000);
    const token = signJwt({
      iss: this.slug, sub: this.slug, aud: `${this.audience}:api`, iat: now, exp: now + 30, jti: randomUUID(),
      htm: method, htu: path, bh: b64url(crypto.createHash('sha256').update(raw).digest()),
    }, this.privateKeyPem, this.kid);
    const res = await fetch(this.baseUrl + path, {
      method, body: raw || undefined,
      headers: { Authorization: `Bearer ${token}`, ...(raw ? { 'Content-Type': 'application/json' } : {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(`${method} ${path} -> ${res.status} ${data?.error?.code ?? ''}`), { status: res.status, data });
    return data;
  }

  ensureTenant(externalTenantId, doc) { return this.request('PUT', `/api/v1/integrations/tenants/${encodeURIComponent(externalTenantId)}/`, doc); }
  setStaffMember(tenantId, userId, role, displayName) { return this.request('PUT', `/api/v1/integrations/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}/`, { role, display_name: displayName }); }
  removeStaffMember(tenantId, userId) { return this.request('DELETE', `/api/v1/integrations/tenants/${encodeURIComponent(tenantId)}/members/${encodeURIComponent(userId)}/`); }
  disableCustomer(tenantId, userId) { return this.request('POST', `/api/v1/integrations/tenants/${encodeURIComponent(tenantId)}/customers/${encodeURIComponent(userId)}/disable/`); }
  archiveTenant(tenantId) { return this.request('DELETE', `/api/v1/integrations/tenants/${encodeURIComponent(tenantId)}/`); }

  /** Identity assertion for the browser to relay (single use, short-lived). Derive every claim from YOUR OWN session. */
  assertion({ actor, sub, tenant, role, name, origin, ttl = 60 }) {
    const now = Math.floor(Date.now() / 1000);
    const claims = { iss: this.slug, aud: `${this.audience}:identity`, sub, actor, iat: now, exp: now + ttl, jti: randomUUID() };
    if (tenant) claims.tenant = tenant;
    if (role) claims.role = role;
    if (name) claims.name = name;
    if (origin) claims.origin = origin;
    return signJwt(claims, this.privateKeyPem, this.kid);
  }
}
