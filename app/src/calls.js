import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

const KEEP_CALLS = 200;       // finished calls kept on disk
const KEEP_EVENTS = 600;      // events kept per saved call (a long call has a few hundred)

// Calls live in memory while they run. Finished calls are saved to `file` (when given) so the call logs survive a restart.
// shortcut: the whole file is rewritten when a call ends; fine for a few hundred calls.
export function createCalls(bookings, { onOffguard, file } = {}) {
  const calls = new Map();
  try {
    if (file) for (const c of JSON.parse(readFileSync(file, 'utf8'))) calls.set(c.id, c);
  } catch { /* first run, or a damaged file: start empty */ }

  const finished = (c) => c.status === 'ended' || c.status === 'failed';
  function persist() {
    if (!file) return;
    const rows = [...calls.values()].filter(finished).sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, KEEP_CALLS)
      .map((c) => ({ ...c, events: c.events.slice(-KEEP_EVENTS) }));
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, JSON.stringify(rows));
  }
  const subs = new Map(); // callId -> Set<fn>

  function create({ bookingId, mode }) {
    if (!bookings.get(bookingId)) return { error: 'not_found' };
    if (!['phone', 'browser'].includes(mode)) return { error: 'invalid_mode' };
    const call = {
      id: 'CALL-' + randomBytes(4).toString('hex'), bookingId, mode, status: 'created', outcome: null,
      slots: {}, events: [], record: null, callSid: null, createdAt: new Date().toISOString(),
    };
    calls.set(call.id, call);
    return { call };
  }

  const view = (c) => c && { ...c, booking: bookings.get(c.bookingId) ?? null };

  function addEvent(id, ev) {
    const c = calls.get(id);
    if (!c) return { error: 'not_found' };
    if (!ev || typeof ev.type !== 'string') return { error: 'invalid_event' };
    const e = { ...ev, ts: new Date().toISOString() };
    c.events.push(e);
    if (e.call_sid) c.callSid = e.call_sid;
    if (e.type === 'status') {
      c.status = e.status;
      if (e.outcome) c.outcome = e.outcome;
    } else if (e.type === 'slot') {
      c.slots[e.key] = e.ok ? { ok: true, value: e.value, display: e.display } : { ok: false, raw: e.raw, error: e.error };
    } else if (e.type === 'offguard') {
      onOffguard?.({ text: e.text, kind: e.kind, state: e.state, intent: e.intent, callId: id, bookingId: c.bookingId });
    } else if (e.type === 'result' && e.record) {
      Object.assign(c, { record: e.record, status: 'ended', outcome: e.record.outcome });
      bookings.applyCallResult(c.bookingId, { call_id: c.id, ...e.record });
    }
    for (const fn of subs.get(id) ?? []) fn(e);
    if (e.type === 'result' || (e.type === 'status' && finished(c))) persist();
    return { event: e };
  }

  function subscribe(id, fn) {
    if (!subs.has(id)) subs.set(id, new Set());
    subs.get(id).add(fn);
    return () => subs.get(id)?.delete(fn);
  }

  // One line per call for the Call logs list, newest first; the events stay on GET /api/calls/:id.
  function list({ bookingId } = {}) {
    return [...calls.values()].filter((c) => !bookingId || c.bookingId === bookingId)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt)).map((c) => {
        const r = c.record ?? {};
        return {
          id: c.id, bookingId: c.bookingId, customerName: bookings.get(c.bookingId)?.customerName ?? null, mode: c.mode,
          status: c.status, outcome: c.outcome, createdAt: c.createdAt, durationS: r.duration_s ?? null,
          costInr: r.cost_inr ?? null, latency: r.latency ?? null, flowVersion: r.flow_version ?? null,
        };
      });
  }

  return {
    create,
    list,
    get: (id) => view(calls.get(id)),
    getBySid: (sid) => view([...calls.values()].find((c) => c.callSid === sid)),
    raw: (id) => calls.get(id),
    setSid: (id, sid) => { const c = calls.get(id); if (c) c.callSid = sid; },
    addEvent,
    subscribe,
  };
}
