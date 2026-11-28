import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';
import { createStore } from './src/bookings.js';
import { createCalls } from './src/calls.js';
import { createOffguard } from './src/offguard.js';
import { createCalculator } from './src/calculator.js';

try { process.loadEnvFile(); } catch {} // .env is optional (git-ignored)

const port = process.env.PORT || 3000;
const store = createStore();
const offguard = createOffguard({ file: process.env.OFFGUARD_FILE || join(import.meta.dirname, 'data', 'offguard.json') });
const calculator = createCalculator({ file: process.env.CALCULATOR_FILE || join(import.meta.dirname, 'data', 'calculator.json') });
const calls = createCalls(store, {
  onOffguard: (x) => offguard.capture(x),
  file: process.env.CALLS_FILE || join(import.meta.dirname, 'data', 'calls.json'),
});
const PUBLIC = join(import.meta.dirname, 'public');
const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf' };
const STATUS = { empty_text: 400, invalid_question: 400, invalid_answer: 400, invalid_intent: 400, invalid_settings: 400, intent_not_teachable: 400, invalid_update: 400, not_found: 404, already_decided: 409, invalid_mode: 400, invalid_event: 400 };

const json = (res, code, body) => {
  res.writeHead(code, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
};

async function readJson(req) {
  let raw = '';
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

function stream(req, res, callId) {
  const call = calls.raw(callId);
  if (!call) return json(res, 404, { error: 'not_found' });
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
  const send = (e) => res.write(`data: ${JSON.stringify(e)}\n\n`);
  call.events.forEach(send);
  const unsub = calls.subscribe(callId, send);
  const keepalive = setInterval(() => res.write(': ping\n\n'), 15000);
  req.on('close', () => { unsub(); clearInterval(keepalive); });
}

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const { pathname } = url;

  if (pathname === '/health') return json(res, 200, { ok: true });
  if (req.method === 'GET' && pathname === '/api/config') return json(res, 200, { botUrl: process.env.BOT_URL || 'http://localhost:7860' });
  if (req.method === 'GET' && pathname === '/api/bookings') return json(res, 200, store.list());

  // Settings of the channel cost calculator.
  if (pathname === '/api/calculator' && req.method === 'GET') return json(res, 200, calculator.get());
  if (pathname === '/api/calculator' && req.method === 'PUT') {
    let body;
    try { body = await readJson(req); } catch { return json(res, 400, { error: 'invalid_json' }); }
    const r = calculator.put(body);
    return r.error ? json(res, STATUS[r.error], r) : json(res, 200, r.settings);
  }
  if (req.method === 'GET' && pathname === '/calculator') return res.writeHead(302, { location: '/calculator.html' }).end();

  // Questions the bot could not handle, and what an admin taught it (the "Questions" view).
  if (req.method === 'GET' && pathname === '/api/offguard') return json(res, 200, offguard.list());
  if (req.method === 'GET' && pathname === '/api/knowledge') return json(res, 200, offguard.knowledge());
  const og = pathname.match(/^\/api\/offguard\/([\w-]+)$/);
  if (og && req.method === 'PATCH') {
    let body;
    try { body = await readJson(req); } catch { return json(res, 400, { error: 'invalid_json' }); }
    const r = offguard.update(og[1], body);
    return r.error ? json(res, STATUS[r.error], r) : json(res, 200, r.item);
  }
  if (req.method === 'GET' && pathname === '/questions') {
    return res.writeHead(302, { location: '/questions.html' }).end();
  }

  // The generated whole-workflow page (tools/flowcheck.py --write), read-only.
  if (req.method === 'GET' && pathname === '/flow') {
    try {
      const html = await readFile(join(import.meta.dirname, '..', 'flows', 'service-scheduling', 'flow.html'));
      res.writeHead(200, { 'content-type': 'text/html' });
      return res.end(html);
    } catch { return json(res, 404, { error: 'not_found' }); }
  }

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

  if (req.method === 'POST' && pathname === '/api/calls') {
    let body;
    try { body = await readJson(req); } catch { return json(res, 400, { error: 'invalid_json' }); }
    const r = calls.create({ bookingId: body.bookingId, mode: body.mode ?? 'browser' });
    if (r.error) return json(res, STATUS[r.error], r);
    return json(res, 201, calls.get(r.call.id));
  }

  if (req.method === 'GET' && pathname === '/api/calls') return json(res, 200, calls.list({ bookingId: url.searchParams.get('bookingId') ?? undefined }));
  if (req.method === 'GET' && pathname === '/logs') return res.writeHead(302, { location: '/logs.html' }).end();

  const sid = pathname.match(/^\/api\/calls\/by-sid\/([\w-]+)$/);
  if (sid && req.method === 'GET') {
    const c = calls.getBySid(sid[1]);
    return c ? json(res, 200, c) : json(res, 404, { error: 'not_found' });
  }

  const c = pathname.match(/^\/api\/calls\/([\w-]+)(?:\/(events|stream))?$/);
  if (c && req.method === 'GET' && !c[2]) {
    const call = calls.get(c[1]);
    return call ? json(res, 200, call) : json(res, 404, { error: 'not_found' });
  }
  if (c && req.method === 'GET' && c[2] === 'stream') return stream(req, res, c[1]);
  if (c && req.method === 'POST' && c[2] === 'events') {
    let body;
    try { body = await readJson(req); } catch { return json(res, 400, { error: 'invalid_json' }); }
    const r = calls.addEvent(c[1], body);
    return r.error ? json(res, STATUS[r.error], r) : json(res, 202, r.event);
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
