import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from './bookings.js';

test('booking can be decided once', () => {
  const s = createStore([{ id: 'A' }, { id: 'B' }]);
  assert.equal(s.confirm('A', { callId: 'c1' }).booking.status, 'confirmed');
  assert.equal(s.reject('A').error, 'already_decided');
  assert.equal(s.reject('B', { reason: 'out of town' }).booking.rejectReason, 'out of town');
  assert.equal(s.confirm('nope').error, 'not_found');
});

test('flow results the dashboard shows are stored, and cleared by a later call', () => {
  const s = createStore([{ id: 'A' }]);
  s.applyCallResult('A', {
    outcome: 'date_requested', call_id: 'c1', requested_slot: { date: '2026-12-03', band: 'morning' },
    will_arrange: ['ladder'], time_window_told: false, slots: {},
  });
  assert.deepEqual(s.get('A').requestedSlot, { date: '2026-12-03', band: 'morning' });
  assert.deepEqual(s.get('A').willArrange, ['ladder']);
  assert.equal(s.get('A').status, 'date_requested');

  s.applyCallResult('A', { outcome: 'slot_confirmed', call_id: 'c2', slots: { visit_date: '2026-11-28', visit_time_slot: 'morning' } });
  assert.equal(s.get('A').requestedSlot, null);
  assert.deepEqual(s.get('A').willArrange, []);
  assert.equal(s.get('A').status, 'confirmed');
});
