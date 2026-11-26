import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from './bookings.js';
import { createCalls } from './calls.js';

const setup = () => {
  const bookings = createStore([{ id: 'BK-1', phone: '+910000000001' }]);
  return { bookings, calls: createCalls(bookings) };
};

test('create validates booking and mode', () => {
  const { calls } = setup();
  assert.equal(calls.create({ bookingId: 'nope', mode: 'browser' }).error, 'not_found');
  assert.equal(calls.create({ bookingId: 'BK-1', mode: 'fax' }).error, 'invalid_mode');
  const { call } = calls.create({ bookingId: 'BK-1', mode: 'browser' });
  assert.match(call.id, /^CALL-/);
  assert.equal(calls.get(call.id).booking.id, 'BK-1');
});

test('events update state, latest slot wins, subscribers notified', () => {
  const { calls } = setup();
  const { call } = calls.create({ bookingId: 'BK-1', mode: 'browser' });
  const seen = [];
  calls.subscribe(call.id, (e) => seen.push(e.type));
  calls.addEvent(call.id, { type: 'status', status: 'in_progress', call_sid: 'SID9' });
  calls.addEvent(call.id, { type: 'slot', key: 'visit_date', ok: false, raw: 'sunday', error: 'Sunday not allowed' });
  calls.addEvent(call.id, { type: 'slot', key: 'visit_date', ok: true, value: '2026-11-28', display: 'Sat 28 Nov' });
  const c = calls.get(call.id);
  assert.equal(c.status, 'in_progress');
  assert.deepEqual(c.slots.visit_date, { ok: true, value: '2026-11-28', display: 'Sat 28 Nov' });
  assert.equal(c.events.length, 3);
  assert.ok(c.events[0].ts);
  assert.deepEqual(seen, ['status', 'slot', 'slot']);
  assert.equal(calls.getBySid('SID9').id, call.id);
  assert.equal(calls.getBySid('other'), undefined);
  assert.equal(calls.addEvent(call.id, {}).error, 'invalid_event');
});

test('result updates booking, and re-calls are allowed', () => {
  const { calls, bookings } = setup();
  const a = calls.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  calls.addEvent(a.id, { type: 'result', record: { outcome: 'callback', slots: {} } });
  assert.equal(bookings.get('BK-1').status, 'callback');
  assert.equal(bookings.get('BK-1').rejectReason, 'callback');

  const b = calls.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  const slots = { visit_date: '2026-11-28', visit_time_slot: '09-12', ladder_available: true, water_source_available: false };
  calls.addEvent(b.id, { type: 'result', record: { outcome: 'confirmed', slots } });
  const bk = bookings.get('BK-1');
  assert.equal(bk.status, 'confirmed');
  assert.equal(bk.callId, b.id);
  assert.equal(bk.visitSlot, '09-12');
  assert.equal(bk.water, false);
  assert.equal(calls.get(b.id).status, 'ended');
  assert.equal(calls.get(b.id).outcome, 'confirmed');
});

test('YAML flow outcomes: slot_confirmed* counts as confirmed, hold is kept', () => {
  const { calls, bookings } = setup();
  const a = calls.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  const slots = { visit_date: '2026-12-03', visit_time_slot: 'evening', ladder_available: true, water_source_available: true, light_available: true };
  calls.addEvent(a.id, { type: 'result', record: { outcome: 'slot_confirmed_alt_day', slots } });
  let bk = bookings.get('BK-1');
  assert.equal(bk.status, 'confirmed');
  assert.equal(bk.outcome, 'slot_confirmed_alt_day');
  assert.equal(bk.visitSlot, 'evening');
  assert.equal(bk.light, true);

  const b = calls.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  const hold = { reason: 'prerequisites_missing', missing_items: ['ladder'], until: '2026-12-05', consent: true };
  calls.addEvent(b.id, { type: 'result', record: { outcome: 'site_on_hold', slots: {}, hold } });
  bk = bookings.get('BK-1');
  assert.equal(bk.status, 'site_on_hold');
  assert.deepEqual(bk.hold, hold);
  const c = calls.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  calls.addEvent(c.id, { type: 'result', record: { outcome: 'slot_confirmed', slots } });
  assert.equal(bookings.get('BK-1').hold, null);
});
