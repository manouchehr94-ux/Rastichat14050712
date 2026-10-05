// Local staging-sandbox helpers (not part of the PR): Django shell access, WebSocket clients with an Origin header.
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import WebSocket from 'ws';
import { expect, type APIRequestContext } from '@playwright/test';
import { BACKEND_URL, WS_URL, ALLOWED_EMBED_ORIGIN, PROJECT_KEY, OPERATOR_PASSWORD } from './env';

export const ORIGIN = ALLOWED_EMBED_ORIGIN;
export function dj(code: string): string {
  return execSync(process.env.STG_DJ!, { input: code, encoding: 'utf8', timeout: 90000 }).trim();
}
export const netDown = () => execSync(`kill -USR1 ${readFileSync(process.env.STG_BALANCER_PIDFILE!, 'utf8').trim()}`);
export const netUp = () => execSync(`kill -USR2 ${readFileSync(process.env.STG_BALANCER_PIDFILE!, 'utf8').trim()}`);

const tokens = new Map<string, string>();
/** nginx limits /auth/login/ to 10/min per address: one login per user per worker process. */
export async function staffToken(request: APIRequestContext, email: string) {
  const c = tokens.get(email); if (c) return c;
  const r = await request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email, password: OPERATOR_PASSWORD } });
  expect(r.status(), await r.text()).toBe(200);
  const access = (await r.json()).access as string; tokens.set(email, access); return access;
}

export async function visitor(request: APIRequestContext) {
  const init = await request.post(`${BACKEND_URL}/api/v1/widget/init/`, { data: { project_key: PROJECT_KEY }, headers: { Origin: ORIGIN } });
  expect(init.ok(), await init.text()).toBeTruthy();
  const token = (await init.json()).session_token as string;
  const headers = { 'X-Widget-Session': token, Origin: ORIGIN };
  const start = await request.post(`${BACKEND_URL}/api/v1/widget/start/`, { data: { session_token: token }, headers });
  expect(start.status()).toBe(200);
  return { token, convId: (await start.json()).id as string, headers };
}

export interface Sock { ws: WebSocket; frames: any[]; closeCode?: number; closedAt?: number; opened: Promise<void>; closed: Promise<number> }
/** Authenticated socket (ticket as first frame) with every frame recorded. Resolves `opened` on auth.ok. */
export function openSocket(url: string, ticket: string, origin?: string): Sock {
  const ws = new WebSocket(url, origin ? { headers: { Origin: origin } } : undefined);
  const s: Sock = { ws, frames: [] } as any;
  s.opened = new Promise((res, rej) => {
    ws.on('open', () => ws.send(JSON.stringify({ type: 'auth', ticket })));
    ws.on('message', (d) => { let f: any; try { f = JSON.parse(String(d)); } catch { f = String(d); } s.frames.push(f); if (f && f.type === 'auth.ok') res(); });
    ws.on('error', rej);
  });
  s.closed = new Promise((res) => ws.on('close', (code) => { s.closeCode = code; s.closedAt = Date.now(); res(code); }));
  return s;
}
export async function widgetSocket(request: APIRequestContext, v: { token: string; convId: string; headers: Record<string, string> }) {
  const t = await request.post(`${BACKEND_URL}/api/v1/widget/ws-ticket/`, { data: { conversation_id: v.convId }, headers: v.headers });
  expect(t.status(), await t.text()).toBe(201);
  const s = openSocket(`${WS_URL}/v2/widget/${v.convId}/`, (await t.json()).ticket, ORIGIN);
  await s.opened; return s;
}
export async function dashboardSocket(request: APIRequestContext, jwt: string, kind: 'dashboard_chat' | 'notifications', convId?: string) {
  const t = await request.post(`${BACKEND_URL}/api/v1/ws/ticket/`, { data: { kind, conversation_id: convId }, headers: { Authorization: `Bearer ${jwt}` } });
  expect(t.status(), await t.text()).toBe(201);
  const path = kind === 'notifications' ? '/v2/notifications/' : `/v2/dashboard/${convId}/`;
  const s = openSocket(`${WS_URL}${path}`, (await t.json()).ticket, 'https://operator-stg.example.test');
  await s.opened; return s;
}
