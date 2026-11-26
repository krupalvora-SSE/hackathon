import { readFileSync } from 'node:fs';

// shortcut: in-memory store reseeded on every restart (handy for demo resets); move to SQLite if state must survive restarts.
const seed = JSON.parse(readFileSync(new URL('../data/bookings.json', import.meta.url)));

export function createStore(rows = seed) {
  const bookings = new Map(rows.map((b) => [b.id, { ...b, status: 'pending', rejectReason: null, callId: null, updatedAt: null }]));

  function decide(id, status, { reason = null, callId = null } = {}) {
    const b = bookings.get(id);
    if (!b) return { error: 'not_found' };
    if (b.status !== 'pending') return { error: 'already_decided', booking: b };
    Object.assign(b, { status, rejectReason: reason, callId, updatedAt: new Date().toISOString() });
    return { booking: b };
  }

  return {
    list: () => [...bookings.values()],
    get: (id) => bookings.get(id),
    confirm: (id, opts) => decide(id, 'confirmed', opts),
    reject: (id, opts) => decide(id, 'rejected', opts),
  };
}
