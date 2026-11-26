import { randomBytes } from 'node:crypto';

// shortcut: in-memory like bookings; calls vanish on restart.
export function createCalls(bookings) {
  const calls = new Map();
  const subs = new Map(); // callId -> Set<fn>

  function create({ bookingId, mode }) {
    if (!bookings.get(bookingId)) return { error: 'not_found' };
    if (mode !== 'browser') return { error: 'invalid_mode' };
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
    } else if (e.type === 'result' && e.record) {
      Object.assign(c, { record: e.record, status: 'ended', outcome: e.record.outcome });
      bookings.applyCallResult(c.bookingId, { call_id: c.id, ...e.record });
    }
    for (const fn of subs.get(id) ?? []) fn(e);
    return { event: e };
  }

  function subscribe(id, fn) {
    if (!subs.has(id)) subs.set(id, new Set());
    subs.get(id).add(fn);
    return () => subs.get(id)?.delete(fn);
  }

  return {
    create,
    get: (id) => view(calls.get(id)),
    getBySid: (sid) => view([...calls.values()].find((c) => c.callSid === sid)),
    raw: (id) => calls.get(id),
    addEvent,
    subscribe,
  };
}
