import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

// The settings of the channel cost calculator, kept on the server so everyone sees the same numbers.
// shortcut: one small JSON file rewritten on every save.
export const DEFAULTS = {
  aiConversionPct: 40,                   // starting point; the call logs' real rate is offered as a reference
  attempts: 1,                           // calls per customer
  aiCostMode: 'logs',                    // 'logs' = average cost per call from the call logs, 'manual' = aiCostPerCall
  aiCostPerCall: null,
  customers: 1000,                       // converted customers per month, to turn the saving into a monthly figure
  channels: [                            // the first one is the human-calling baseline the saving is measured against
    { name: 'Manual calling', costPerConversion: 24 },
    { name: 'Push notification', costPerConversion: 0 },
    { name: 'Whatsapp', costPerConversion: 1.1 },
  ],
};

// null / blank stays null; anything else must be a finite number inside the bounds.
const num = (v, min, max) => {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= min && n <= max ? n : NaN;
};

export function validate(b = {}) {
  const out = {
    aiConversionPct: num(b.aiConversionPct, 0.1, 100),
    attempts: num(b.attempts ?? 1, 1, 10) ?? 1,
    aiCostMode: b.aiCostMode === 'manual' ? 'manual' : 'logs',
    aiCostPerCall: num(b.aiCostPerCall, 0, 100000),
    customers: num(b.customers ?? DEFAULTS.customers, 1, 100000000) ?? DEFAULTS.customers,
    channels: [],
  };
  for (const k of ['aiConversionPct', 'attempts', 'aiCostPerCall', 'customers']) if (Number.isNaN(out[k])) return { error: 'invalid_settings', field: k };
  if (!Array.isArray(b.channels) || b.channels.length !== 3) return { error: 'invalid_settings', field: 'channels' };
  for (const [i, c] of b.channels.entries()) {
    const name = String(c?.name ?? '').replace(/\s+/g, ' ').trim().slice(0, 40);
    const cost = num(c?.costPerConversion, 0, 1000000);
    if (!name || Number.isNaN(cost)) return { error: 'invalid_settings', field: `channels[${i}]` };
    out.channels.push({ name, costPerConversion: cost });
  }
  return { settings: out };
}

export function createCalculator({ file } = {}) {
  let settings = structuredClone(DEFAULTS);
  try { if (file) settings = validate(JSON.parse(readFileSync(file, 'utf8'))).settings ?? settings; } catch { /* first run or a damaged file */ }
  return {
    get: () => settings,
    put(body) {
      const r = validate(body);
      if (r.error) return r;
      settings = r.settings;
      if (file) { mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, JSON.stringify(settings, null, 2)); }
      return { settings };
    },
  };
}
