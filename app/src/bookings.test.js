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
