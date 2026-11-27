// The maths of the channel cost calculator, shared by the page and the tests (no DOM in here).
const CONFIRMED = ['confirmed', 'slot_confirmed', 'slot_confirmed_alt_band', 'slot_confirmed_alt_day'];
const ok = (...xs) => xs.every((x) => typeof x === 'number' && Number.isFinite(x));

// What one converted customer costs through AI calling: every customer takes `attempts` calls on average
// and only `conversionPct` % of customers convert.
export const costPerConversion = ({ costPerCall, attempts = 1, conversionPct }) =>
  ok(costPerCall, attempts, conversionPct) && costPerCall >= 0 && attempts >= 1 && conversionPct > 0
    ? (costPerCall * attempts) / (conversionPct / 100) : null;

// The conversion rate (%) at which AI calling costs the same as a channel that costs `otherCost` per conversion.
// Above it AI calling is cheaper; a result above 100 means it cannot win at this cost per call.
export const breakEvenPct = ({ costPerCall, attempts = 1, otherCost }) =>
  ok(costPerCall, attempts, otherCost) && costPerCall >= 0 && attempts >= 1 && otherCost > 0
    ? ((costPerCall * attempts) / otherCost) * 100 : null;

// What AI calling saves against another channel: per converted customer, as a share of that channel's cost, and over `customers`.
// A negative saving means AI calling costs more.
export const savings = ({ aiCost, otherCost, customers }) =>
  ok(aiCost, otherCost) && otherCost > 0
    ? { perCustomer: otherCost - aiCost, pct: ((otherCost - aiCost) / otherCost) * 100, total: ok(customers) ? (otherCost - aiCost) * customers : null }
    : null;

// Cheapest first; channels without a number yet go last.
export const rank = (rows) => [...rows].sort((a, b) => (a.cost ?? Infinity) - (b.cost ?? Infinity));

// What the call logs say: average cost over finished calls that recorded one, and the share that ended confirmed.
export function observed(calls) {
  const done = calls.filter((c) => c.status === 'ended' || c.status === 'failed');
  const costs = done.map((c) => c.costInr).filter((x) => typeof x === 'number');
  return {
    finished: done.length,
    costCalls: costs.length,
    costPerCall: costs.length ? costs.reduce((a, b) => a + b, 0) / costs.length : null,
    confirmedRate: done.length ? done.filter((c) => CONFIRMED.includes(c.outcome)).length / done.length : null,
    confirmed: done.filter((c) => CONFIRMED.includes(c.outcome)).length,
  };
}
