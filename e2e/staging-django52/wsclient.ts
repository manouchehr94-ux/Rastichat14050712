import WebSocket from 'ws';
import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { BACKEND_URL, WS_URL } from './env';

/** Synthetic seed written by the stack harness (ids and staging-only credentials; never real data). */
export interface Seed {
  users: Record<string, { email: string; password: string; id: string }>;
  A: { workspace: string; project_key: string; conversation: string; session_token: string; visitor: string };
  B: { workspace: string; project_key: string; conversation: string; session_token: string; visitor: string };
  support_conversation_a: string;
}
export function seed(): Seed {
  const path = process.env.DJANGO52_SEED_JSON;
  if (!path) throw new Error('DJANGO52_SEED_JSON (the harness seed-extra.json) is required for this spec');
  return JSON.parse(readFileSync(path, 'utf8'));
}

/** Server-side state change through the harness (deactivate user, remove membership, revoke session, ...). */
export function admin(action: string, arg = ''): void {
  const cmd = process.env.DJANGO52_ADMIN_CMD;
  if (!cmd) throw new Error('DJANGO52_ADMIN_CMD is required for this spec');
  execSync(`${cmd} ${action} ${JSON.stringify(arg)}`, { stdio: 'ignore', timeout: 120000 });
}

export const ORIGINS = {
  operator: 'https://operator-stg.example.test',
  allowedEmbed: process.env.DJANGO52_ALLOWED_EMBED_ORIGIN || 'https://embed-allowed.example.test',
  forbiddenEmbed: process.env.DJANGO52_FORBIDDEN_EMBED_ORIGIN || 'https://embed-forbidden.example.test',
};

export interface Sock {
  ws: WebSocket;
  frames: Record<string, unknown>[];
  /** resolves with the close code (4401/4403/1006...) */
  closed: Promise<number>;
  /** resolves when `auth.ok` arrives, rejects if the socket closes first */
  authed: Promise<void>;
  isClosed: () => boolean;
}

export function openSocket(url: string, opts: { origin: string; ticket?: string; localAddress?: string }): Sock {
  const ws = new WebSocket(url, { headers: { Origin: opts.origin }, localAddress: opts.localAddress });
  const frames: Record<string, unknown>[] = [];
  let closedFlag = false;
  let handshakeStatus = '';
  let resolveAuthed!: () => void;
  let rejectAuthed!: (e: Error) => void;
  const authed = new Promise<void>((res, rej) => { resolveAuthed = res; rejectAuthed = rej; });
  authed.catch(() => undefined);
  const closed = new Promise<number>((res) => {
    // with a listener registered `ws` no longer aborts a refused handshake by itself: do it, and keep the HTTP status for the message
    ws.on('unexpected-response', (_req, resp) => { handshakeStatus = ` (handshake HTTP ${resp.statusCode})`; resp.resume(); ws.terminate(); });
    ws.on('close', (code) => { closedFlag = true; rejectAuthed(new Error(`closed ${code} before auth.ok${handshakeStatus}`)); res(code); });
    ws.on('error', () => undefined);
  });
  ws.on('open', () => { if (opts.ticket) ws.send(JSON.stringify({ type: 'auth', ticket: opts.ticket })); });
  ws.on('message', (data) => {
    const frame = JSON.parse(String(data));
    frames.push(frame);
    if (frame.type === 'auth.ok') resolveAuthed();
  });
  return { ws, frames, closed, authed, isClosed: () => closedFlag };
}

const jwtCache = new Map<string, { jwt: string; at: number }>();

/**
 * Staff JWT via the real login API. nginx rate-limits /auth/login/ to 10/min per IP exactly as in production, so tokens are
 * cached per user for the run (a JWT lives for an hour) and a 503 from the limiter is waited out instead of failing the spec.
 */
export async function loginApi(email: string, password: string, fresh = false): Promise<string> {
  const hit = jwtCache.get(email);
  if (!fresh && hit && Date.now() - hit.at < 20 * 60000) return hit.jwt;
  for (let attempt = 0; attempt < 8; attempt++) {
    const res = await fetch(`${BACKEND_URL}/api/v1/auth/login/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
    if (res.status === 200) { const jwt = (await res.json()).access; jwtCache.set(email, { jwt, at: Date.now() }); return jwt; }
    if (res.status !== 503 && res.status !== 429) throw new Error(`login ${email} -> ${res.status}`);
    await new Promise((r) => setTimeout(r, 7000));
  }
  throw new Error(`login ${email} -> still rate limited`);
}

export async function staffTicket(jwt: string, kind: string, conversationId?: string): Promise<{ status: number; ticket?: string }> {
  const res = await fetch(`${BACKEND_URL}/api/v1/ws/ticket/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwt}` },
    body: JSON.stringify({ kind, ...(conversationId ? { conversation_id: conversationId } : {}) }),
  });
  return { status: res.status, ticket: res.status === 201 ? (await res.json()).ticket : undefined };
}

export async function widgetTicket(sessionToken: string, conversationId: string, origin = ORIGINS.allowedEmbed): Promise<{ status: number; ticket?: string }> {
  const res = await fetch(`${BACKEND_URL}/api/v1/widget/ws-ticket/`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: origin, 'X-Widget-Session': sessionToken },
    body: JSON.stringify({ conversation_id: conversationId }),
  });
  return { status: res.status, ticket: res.status === 201 ? (await res.json()).ticket : undefined };
}

export async function widgetHistory(sessionToken: string, conversationId: string, origin = ORIGINS.allowedEmbed): Promise<number> {
  const res = await fetch(`${BACKEND_URL}/api/v1/widget/conversations/${conversationId}/messages/`, { headers: { Origin: origin, 'X-Widget-Session': sessionToken } });
  return res.status;
}

export const wsUrl = (path: string) => `${WS_URL}/ws${path}`;
export const withTimeout = <T>(p: Promise<T>, ms: number, what: string) =>
  Promise.race([p, new Promise<T>((_, rej) => setTimeout(() => rej(new Error(`timeout: ${what}`)), ms))]);
