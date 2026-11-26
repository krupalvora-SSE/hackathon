import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { createStore } from './src/bookings.js';

try { process.loadEnvFile(); } catch {} // .env is optional (git-ignored)

const port = process.env.PORT || 3000;
const store = createStore();
const PUBLIC = join(import.meta.dirname, 'public');
const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf' };
const STATUS = { not_found: 404, already_decided: 409 };

const json = (res, code, body) => {
  res.writeHead(code, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
};

async function readJson(req) {
  let raw = '';
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

createServer(async (req, res) => {
  const { pathname } = new URL(req.url, 'http://x');

  if (pathname === '/health') return json(res, 200, { ok: true });
  if (req.method === 'GET' && pathname === '/api/bookings') return json(res, 200, store.list());

  const m = pathname.match(/^\/api\/bookings\/([\w-]+)(?:\/(confirm|reject))?$/);
  if (m && req.method === 'GET' && !m[2]) {
    const b = store.get(m[1]);
    return b ? json(res, 200, b) : json(res, 404, { error: 'not_found' });
  }
  if (m && req.method === 'POST' && m[2]) {
    let body;
    try { body = await readJson(req); } catch { return json(res, 400, { error: 'invalid_json' }); }
    const r = store[m[2]](m[1], { reason: body.reason ?? null, callId: body.callId ?? null });
    return r.error ? json(res, STATUS[r.error], r) : json(res, 200, r.booking);
  }

  if (req.method === 'GET') {
    const file = normalize(join(PUBLIC, pathname === '/' ? 'index.html' : pathname));
    if (!file.startsWith(PUBLIC)) return json(res, 403, { error: 'forbidden' });
    try {
      const data = await readFile(file);
      res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
      return res.end(data);
    } catch {}
  }
  json(res, 404, { error: 'not_found' });
}).listen(port, () => console.log(`listening on http://localhost:${port}`));
