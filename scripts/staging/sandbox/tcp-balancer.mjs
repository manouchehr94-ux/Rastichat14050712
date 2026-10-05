// Layer-4 round-robin balancer for the sandbox staging stack: nginx -> :8100 -> daphne :8101/:8102.
// (An HTTP-level balancer consumed Daphne's X-Accel-Redirect header; this one relays raw bytes.)
// SIGUSR1 = simulate a network outage (destroy every relayed connection and refuse new ones), SIGUSR2 = network back.
// Needed because Chromium's Network.emulateNetworkConditions(offline) does NOT close an already-open WebSocket.
import net from 'node:net';
const targets = (process.env.TARGETS || '127.0.0.1:8101,127.0.0.1:8102').split(',').map(t => { const [h,p]=t.split(':'); return {host:h,port:+p}; });
let i = 0, down = false; const live = new Set();
net.createServer((c) => {
  if (down) { c.destroy(); return; }
  const t = targets[Math.floor(Math.random() * targets.length)]; // random, not strict round robin: with per-request TCP connections strict RR pins WebSockets to one worker (parity)
  const u = net.connect(t.port, t.host);
  live.add(c); live.add(u);
  c.pipe(u); u.pipe(c);
  const end = () => { c.destroy(); u.destroy(); live.delete(c); live.delete(u); };
  c.on('error', end); u.on('error', end); c.on('close', end); u.on('close', end);
}).listen(+(process.env.PORT || 8100), '127.0.0.1');
process.on('SIGUSR1', () => { down = true; for (const s of live) s.destroy(); console.log(new Date().toISOString(), 'balancer: DOWN'); });
process.on('SIGUSR2', () => { down = false; console.log(new Date().toISOString(), 'balancer: UP'); });
