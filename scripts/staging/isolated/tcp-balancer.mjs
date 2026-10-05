// L4 round-robin balancer for the isolated staging: 127.0.0.1:<listen> -> daphne workers.
// Stands in for a multi-worker upstream WITHOUT being an HTTP proxy, so the front nginx sees Daphne's response headers
// untouched (an HTTP-level balancer would swallow X-Accel-Redirect).
import net from 'node:net'
const [listen, ...ports] = process.argv.slice(2).map(Number)
let i = 0
net.createServer(c => {
  const s = net.connect(ports[i++ % ports.length], '127.0.0.1')
  c.pipe(s).pipe(c)
  const end = () => { c.destroy(); s.destroy() }
  c.on('error', end); s.on('error', end); c.on('close', end); s.on('close', end)
}).listen(listen, '127.0.0.1')
