import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { breakEvenPct, costPerConversion, observed, rank } from '../public/calculator.js';
import { createCalculator, DEFAULTS, validate } from './calculator.js';

const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} !== ${b}`);

test('cost per conversion = cost per call x calls per customer / conversion rate', () => {
  close(costPerConversion({ costPerCall: 1.23, conversionPct: 40 }), 3.075);                         // 1.23 / 0.4
  close(costPerConversion({ costPerCall: 1.23, attempts: 1.5, conversionPct: 40 }), 4.6125);          // 1.23 x 1.5 / 0.4
  close(costPerConversion({ costPerCall: 2, attempts: 2, conversionPct: 100 }), 4);
  close(costPerConversion({ costPerCall: 0, conversionPct: 10 }), 0);
});

test('a missing, zero or impossible input gives no number instead of a wrong one', () => {
  for (const bad of [{ costPerCall: null, conversionPct: 40 }, { costPerCall: 1, conversionPct: 0 }, { costPerCall: 1, conversionPct: -5 },
    { costPerCall: 1, conversionPct: undefined }, { costPerCall: -1, conversionPct: 40 }, { costPerCall: 1, attempts: 0.5, conversionPct: 40 },
    { costPerCall: NaN, conversionPct: 40 }, { costPerCall: '1', conversionPct: 40 }]) {
    assert.equal(costPerConversion(bad), null, JSON.stringify(bad));
  }
});

test('break-even is the conversion rate at which AI calling costs the same as another channel', () => {
  close(breakEvenPct({ costPerCall: 1.23, otherCost: 10 }), 12.3);                                     // 1.23 / 10
  close(breakEvenPct({ costPerCall: 1.23, attempts: 1.5, otherCost: 10 }), 18.45);
  assert.ok(breakEvenPct({ costPerCall: 5, otherCost: 2 }) > 100);                                     // cannot win at this cost per call
  assert.equal(breakEvenPct({ costPerCall: 1, otherCost: 0 }), null);
  assert.equal(breakEvenPct({ costPerCall: 1, otherCost: null }), null);
  // and it is consistent with the cost: at break-even both channels cost the same
  const be = breakEvenPct({ costPerCall: 1.23, attempts: 1.5, otherCost: 10 });
  close(costPerConversion({ costPerCall: 1.23, attempts: 1.5, conversionPct: be }), 10);
});

test('channels are ranked cheapest first, those without a number last', () => {
  const rows = [{ n: 'a', cost: 9 }, { n: 'b', cost: null }, { n: 'c', cost: 3 }, { n: 'd', cost: 5 }];
  assert.deepEqual(rank(rows).map((r) => r.n), ['c', 'd', 'a', 'b']);
});

test('the call logs give the cost per call and the confirmed rate over finished calls', () => {
  const calls = [
    { status: 'ended', outcome: 'slot_confirmed', costInr: 1.42 }, { status: 'ended', outcome: 'slot_confirmed_alt_day', costInr: 2.0 },
    { status: 'ended', outcome: 'site_on_hold', costInr: 1.0 }, { status: 'ended', outcome: 'recall_requested', costInr: 0.5 },
    { status: 'failed', outcome: null, costInr: null }, { status: 'in_progress', outcome: null, costInr: null },
  ];
  const o = observed(calls);
  assert.equal(o.finished, 5); assert.equal(o.costCalls, 4); assert.equal(o.confirmed, 2);
  close(o.costPerCall, 1.23); close(o.confirmedRate, 0.4);
  assert.deepEqual(observed([]), { finished: 0, costCalls: 0, costPerCall: null, confirmedRate: null, confirmed: 0 });
  assert.equal(observed([{ status: 'in_progress' }]).confirmedRate, null);                           // nothing finished yet
});

// ---- the saved settings
const good = () => ({ aiConversionPct: 35, attempts: 1.4, aiCostMode: 'manual', aiCostPerCall: 2.5,
  channels: [{ name: 'Manual calling', costPerConversion: 40 }, { name: 'Push', costPerConversion: 6 }, { name: 'WhatsApp', costPerConversion: 12 }] });

test('valid settings are accepted and tidied', () => {
  const r = validate({ ...good(), channels: [{ name: '  Manual   calling ', costPerConversion: '40' }, ...good().channels.slice(1)] });
  assert.equal(r.settings.channels[0].name, 'Manual calling');
  assert.equal(r.settings.channels[0].costPerConversion, 40);
  assert.equal(r.settings.attempts, 1.4);
});

test('blank numbers stay blank and attempts defaults to 1', () => {
  const r = validate({ ...good(), aiConversionPct: '', attempts: null, aiCostPerCall: null,
    channels: good().channels.map((c) => ({ ...c, costPerConversion: '' })) });
  assert.equal(r.settings.aiConversionPct, null); assert.equal(r.settings.attempts, 1); assert.equal(r.settings.aiCostPerCall, null);
  assert.ok(r.settings.channels.every((c) => c.costPerConversion === null));
});

test('settings outside the limits are refused and name the field', () => {
  const cases = [
    [{ aiConversionPct: 0 }, 'aiConversionPct'], [{ aiConversionPct: 101 }, 'aiConversionPct'], [{ aiConversionPct: 'abc' }, 'aiConversionPct'],
    [{ attempts: 0.5 }, 'attempts'], [{ attempts: 11 }, 'attempts'], [{ aiCostPerCall: -1 }, 'aiCostPerCall'],
    [{ channels: good().channels.slice(0, 2) }, 'channels'], [{ channels: 'x' }, 'channels'],
    [{ channels: [{ name: '', costPerConversion: 1 }, ...good().channels.slice(1)] }, 'channels[0]'],
    [{ channels: [good().channels[0], { name: 'x', costPerConversion: -3 }, good().channels[2]] }, 'channels[1]'],
  ];
  for (const [patch, field] of cases) assert.deepEqual(validate({ ...good(), ...patch }), { error: 'invalid_settings', field }, JSON.stringify(patch));
});

test('settings survive a restart, a refused save changes nothing, and a damaged file falls back to the defaults', () => {
  const dir = mkdtempSync(join(tmpdir(), 'calc-'));
  const file = join(dir, 'sub', 'calculator.json');
  const a = createCalculator({ file });
  assert.deepEqual(a.get(), DEFAULTS);
  assert.ok(a.put(good()).settings);
  assert.equal(a.put({ ...good(), attempts: 99 }).error, 'invalid_settings');
  assert.equal(a.get().attempts, 1.4);
  assert.deepEqual(createCalculator({ file }).get(), good());
  writeFileSync(file, '{ nope');
  assert.deepEqual(createCalculator({ file }).get(), DEFAULTS);
  rmSync(dir, { recursive: true });
});
