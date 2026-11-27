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
    will_arrange: ['ladder'], site_issues: ['rusting on the frame'], time_window_told: false, slots: {},
  });
  assert.deepEqual(s.get('A').requestedSlot, { date: '2026-12-03', band: 'morning' });
  assert.deepEqual(s.get('A').willArrange, ['ladder']);
  assert.deepEqual(s.get('A').siteIssues, ['rusting on the frame']);
  assert.equal(s.get('A').status, 'date_requested');

  s.applyCallResult('A', { outcome: 'slot_confirmed', call_id: 'c2', slots: { visit_date: '2026-11-28', visit_time_slot: 'morning' } });
  assert.equal(s.get('A').requestedSlot, null);
  assert.deepEqual(s.get('A').willArrange, []);
  assert.deepEqual(s.get('A').siteIssues, []);
  assert.equal(s.get('A').status, 'confirmed');
});

test('what was confirmed is kept whatever the outcome: readiness, hold, call back, who we spoke to', () => {
  const s = createStore([{ id: 'A' }]);
  s.applyCallResult('A', {
    outcome: 'site_on_hold', call_id: 'c1', spoke_to: 'family_member', recall_window: null,
    hold: { reason: 'prerequisites_missing', until: '2026-12-10', consent: true },
    slots: { ladder_available: false, water_source_available: true, light_available: true },
  });
  const b = s.get('A');
  assert.equal(b.status, 'site_on_hold');
  assert.deepEqual(b.readiness, { ladder: false, water: true, light: true });
  assert.equal(b.hold.until, '2026-12-10');
  assert.equal(b.spokeTo, 'family_member');
  s.applyCallResult('A', { outcome: 'recall_requested', call_id: 'c2', recall_window: 'kal shaam 5 se 7', spoke_to: { name: 'x' }, slots: {} });
  assert.equal(s.get('A').recallWindow, 'kal shaam 5 se 7');
  assert.equal(s.get('A').spokeTo, 'customer');                            // an old record shape still works
  assert.deepEqual(s.get('A').readiness, { ladder: null, water: null, light: null });
  assert.equal(s.get('A').hold, null);                                     // a later call clears the earlier hold
});
