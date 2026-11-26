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

  // Voice-agent outcome: always applies (re-calls allowed), unlike the one-shot confirm/reject above.
  function applyCallResult(id, record) {
    const b = bookings.get(id);
    if (!b) return { error: 'not_found' };
    // checklist bot says 'confirmed'; the YAML flow says slot_confirmed / slot_confirmed_alt_band / _alt_day
    const ok = record.outcome === 'confirmed' || String(record.outcome).startsWith('slot_confirmed');
    const s = record.slots ?? {};
    Object.assign(b, {
      status: ok ? 'confirmed' : record.outcome,
      outcome: record.outcome,
      rejectReason: ok ? null : record.outcome,
      callId: record.call_id ?? null,
      updatedAt: new Date().toISOString(),
      ...(ok && {
        visitDate: s.visit_date, visitSlot: s.visit_time_slot,
        ladder: s.ladder_available, water: s.water_source_available, light: s.light_available ?? null,
      }),
      hold: record.hold ?? null,
      // new flow results: kept per call, so a later call clears them
      requestedSlot: record.requested_slot ?? null,
      willArrange: record.will_arrange ?? [],
      timeWindowTold: record.time_window_told ?? null,
    });
    return { booking: b };
  }

  return {
    list: () => [...bookings.values()],
    get: (id) => bookings.get(id),
    confirm: (id, opts) => decide(id, 'confirmed', opts),
    reject: (id, opts) => decide(id, 'rejected', opts),
    applyCallResult,
  };
}
