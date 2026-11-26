import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createOffguard } from './offguard.js';
import { createCalls } from './calls.js';
import { createStore } from './bookings.js';

const q = (text, extra = {}) => ({ text, kind: 'deflected', state: 'OFFER_PRIMARY', callId: 'C1', bookingId: 'B1', ...extra });

test('the same question is stored once and counted, however it is spaced or cased', () => {
  const o = createOffguard();
  o.capture(q('Technician ka number milega?'));
  o.capture(q('  technician  ka NUMBER milega? '));
  o.capture(q('Kitne din lagenge?'));
  assert.equal(o.list().length, 2);
  assert.equal(o.list()[0].count, 2);                       // the most asked comes first
  assert.equal(o.list()[0].status, 'new');
});

test('empty text is refused and an unknown reason becomes unclear', () => {
  const o = createOffguard();
  assert.equal(o.capture(q('   ')).error, 'empty_text');
  assert.equal(o.capture(q('hmm', { kind: 'weird' })).item.kind, 'unclear');
  assert.equal(o.capture(q('x'.repeat(500))).item.text.length, 300);
});

test('an answer makes an approved answer the bot can read; the question can be generalised first', () => {
  const o = createOffguard();
  const { item } = o.capture(q('Technician ka number milega kya?'));
  assert.equal(o.update(item.id, { question: 'Technician ka number', answer: 'Technician visit wale din call karega.' }).item.status, 'answered');
  assert.deepEqual(o.knowledge(), {
    faqs: [{ id: item.id, question: 'Technician ka number', answer: 'Technician visit wale din call karega.' }],
    intents: {},
  });
});

test('teaching an intent adds the phrase to that intent, and replaces an earlier answer', () => {
  const o = createOffguard();
  const a = o.capture(q('manager se baat karao')).item;
  const b = o.capture(q('Technician ka number milega?')).item;
  o.update(b.id, { answer: 'Call karega.' });
  o.update(b.id, { teachIntent: 'wants_human' });
  o.update(a.id, { teachIntent: 'wants_human' });
  assert.deepEqual(o.knowledge(), { faqs: [], intents: { wants_human: ['manager se baat karao', 'Technician ka number milega?'] } });
  assert.equal(o.list().find((x) => x.id === b.id).answer, null);
});

test('ignored and reopened questions are not taught', () => {
  const o = createOffguard();
  const { item } = o.capture(q('Apple khaya?'));
  o.update(item.id, { answer: 'x' });
  assert.equal(o.update(item.id, { status: 'ignored' }).item.status, 'ignored');
  assert.deepEqual(o.knowledge(), { faqs: [], intents: {} });
  assert.equal(o.update(item.id, { status: 'new' }).item.status, 'new');
});

test('bad updates are refused without changing anything', () => {
  const o = createOffguard();
  const { item } = o.capture(q('hello'));
  assert.equal(o.update(item.id, { answer: '   ' }).error, 'invalid_answer');
  assert.equal(o.update(item.id, { teachIntent: 'Wants Human; drop' }).error, 'invalid_intent');
  assert.equal(o.update(item.id, { question: '' }).error, 'invalid_question');
  assert.equal(o.update(item.id, {}).error, 'invalid_update');
  assert.equal(o.update(item.id, { status: 'answered' }).error, 'invalid_update');     // an answer is needed for that
  assert.equal(o.update('Q-nope', { answer: 'a' }).error, 'not_found');
  assert.equal(o.list()[0].status, 'new');
});

test('everything survives a restart, and a damaged file just starts empty', () => {
  const dir = mkdtempSync(join(tmpdir(), 'og-'));
  const file = join(dir, 'sub', 'offguard.json');
  const a = createOffguard({ file });
  const { item } = a.capture(q('Technician ka number?'));
  a.update(item.id, { answer: 'Call karega.' });
  assert.equal(JSON.parse(readFileSync(file, 'utf8'))[0].answer, 'Call karega.');
  const b = createOffguard({ file });
  assert.equal(b.knowledge().faqs[0].answer, 'Call karega.');
  rmSync(file); 
  assert.deepEqual(createOffguard({ file }).list(), []);
  rmSync(dir, { recursive: true });
});

test('an offguard event from a call is captured with the call and booking', () => {
  const store = createStore([{ id: 'BK-1', customerName: 'R', phone: '1' }]);
  const o = createOffguard();
  const calls = createCalls(store, { onOffguard: (x) => o.capture(x) });
  const { call } = calls.create({ bookingId: 'BK-1', mode: 'browser' });
  calls.addEvent(call.id, { type: 'offguard', kind: 'deflected', text: 'Subsidy kab milegi?', state: 'OFFER_PRIMARY', intent: 'price_or_offtopic' });
  const [item] = o.list();
  assert.deepEqual([item.text, item.kind, item.state, item.callId, item.bookingId], ['Subsidy kab milegi?', 'deflected', 'OFFER_PRIMARY', call.id, 'BK-1']);
  assert.equal(calls.get(call.id).events.at(-1).type, 'offguard');       // still shown in the call's own feed
});

test('calls work without a capture hook', () => {
  const store = createStore([{ id: 'BK-1', customerName: 'R', phone: '1' }]);
  const calls = createCalls(store);
  const { call } = calls.create({ bookingId: 'BK-1', mode: 'browser' });
  assert.ok(calls.addEvent(call.id, { type: 'offguard', text: 'x', kind: 'unclear' }).event);
});
