import { test } from 'node:test';
import { mkdtempSync, rmSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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

const rows = [{ id: 'BK-1', customerName: 'Rahul Joshi', phone: '1' }, { id: 'BK-2', customerName: 'Sneha K', phone: '2' }];
const record = (extra = {}) => ({ outcome: 'slot_confirmed', duration_s: 66.2, cost_inr: 1.88, latency: { p50: 900, p95: 1500, turns: 8 },
  flow_version: '0.17.0', slots: { ladder_available: true, water_source_available: true, light_available: false }, ...extra });

test('the call list is one summary per call, newest first, and can be filtered by booking', async () => {
  const calls = createCalls(createStore(rows));
  const a = calls.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  await new Promise((r) => setTimeout(r, 5));
  const b = calls.create({ bookingId: 'BK-2', mode: 'phone' }).call;
  calls.addEvent(a.id, { type: 'result', record: record() });
  const all = calls.list();
  assert.deepEqual(all.map((c) => c.id), [b.id, a.id]);
  assert.deepEqual(all[1], { id: a.id, bookingId: 'BK-1', customerName: 'Rahul Joshi', mode: 'browser', status: 'ended',
    outcome: 'slot_confirmed', createdAt: a.createdAt, durationS: 66.2, costInr: 1.88, latency: { p50: 900, p95: 1500, turns: 8 }, flowVersion: '0.17.0', offguardCount: 0 });
  assert.equal(all[0].status, 'created');                                  // still running: no duration yet
  assert.deepEqual(calls.list({ bookingId: 'BK-1' }).map((c) => c.id), [a.id]);
  assert.ok(!('events' in all[0]));                                       // the events stay on the single-call endpoint
});

test('finished calls are saved and come back after a restart, running ones are not', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calls-'));
  const file = join(dir, 'data', 'calls.json');
  const one = createCalls(createStore(rows), { file });
  const done = one.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  const running = one.create({ bookingId: 'BK-2', mode: 'phone' }).call;
  one.addEvent(done.id, { type: 'transcript', role: 'agent', text: 'Hello?' });
  one.addEvent(done.id, { type: 'beatplan', action: 'confirm_slot', status: 'stub', payload: {} });
  assert.equal(existsSync(file), false);                                   // nothing is written while calls run
  one.addEvent(done.id, { type: 'result', record: record() });
  one.addEvent(running.id, { type: 'status', status: 'in_progress' });

  const two = createCalls(createStore(rows), { file });                    // a restart
  assert.deepEqual(two.list().map((c) => c.id), [done.id]);
  const back = two.get(done.id);
  assert.equal(back.record.cost_inr, 1.88);
  assert.deepEqual(back.events.map((e) => e.type), ['transcript', 'beatplan', 'result']);
  assert.equal(back.booking.customerName, 'Rahul Joshi');
  rmSync(dir, { recursive: true });
});

test('a failed call is saved too, and a damaged file just starts empty', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calls-'));
  const file = join(dir, 'calls.json');
  const one = createCalls(createStore(rows), { file });
  const c = one.create({ bookingId: 'BK-1', mode: 'browser' }).call;
  one.addEvent(c.id, { type: 'status', status: 'failed', error: 'KeyError' });
  assert.equal(createCalls(createStore(rows), { file }).list()[0].status, 'failed');
  writeFileSync(file, '{ not json');
  assert.deepEqual(createCalls(createStore(rows), { file }).list(), []);
  rmSync(dir, { recursive: true });
});

test('only the newest 200 finished calls are kept on disk', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calls-'));
  const file = join(dir, 'calls.json');
  const calls = createCalls(createStore(rows), { file });
  for (let i = 0; i < 205; i++) {
    const { call } = calls.create({ bookingId: 'BK-1', mode: 'browser' });
    calls.addEvent(call.id, { type: 'result', record: record() });
  }
  assert.equal(createCalls(createStore(rows), { file }).list().length, 200);
  rmSync(dir, { recursive: true });
});
