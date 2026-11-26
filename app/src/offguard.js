import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomBytes } from 'node:crypto';

// Things customers said that the bot could not handle, and what an admin taught it about them.
// shortcut: one JSON file rewritten on every change; fine for hundreds of rows, move to a database beyond that.
export const KINDS = ['deflected', 'not_understood', 'unclear', 'other_language'];
export const STATUSES = ['new', 'answered', 'taught', 'ignored'];
// Intents whose behaviour is "I can't help": teaching a phrase to one of them makes the bot deflect that question forever.
export const NOT_TEACHABLE = ['unclear', 'known_question', 'price_or_offtopic'];

const norm = (t) => String(t ?? '').replace(/\s+/g, ' ').trim();
const same = (a, b) => norm(a).toLowerCase() === norm(b).toLowerCase();

export function createOffguard({ file } = {}) {
  let items = [];
  try { if (file) items = JSON.parse(readFileSync(file, 'utf8')); } catch { /* first run, or a damaged file: start empty */ }
  const save = () => {
    if (!file) return;
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, JSON.stringify(items, null, 2));
  };

  function capture({ text, kind, state = null, intent = null, callId = null, bookingId = null }) {
    const t = norm(text).slice(0, 300);
    if (!t) return { error: 'empty_text' };
    const now = new Date().toISOString();
    let item = items.find((x) => same(x.text, t));
    if (item) {
      Object.assign(item, { count: item.count + 1, lastSeen: now, lastCallId: callId, lastState: state });
    } else {
      item = {
        id: 'Q-' + randomBytes(3).toString('hex'), text: t, question: t,
        kind: KINDS.includes(kind) ? kind : 'unclear', state, intent, count: 1,
        firstSeen: now, lastSeen: now, callId, lastCallId: callId, bookingId, lastState: state,
        status: 'new', answer: null, teachIntent: null,
      };
      items.push(item);
    }
    save();
    return { item };
  }

  // Newest-and-most-asked first, so the admin starts with what matters.
  const list = () => [...items].sort((a, b) => b.count - a.count || b.lastSeen.localeCompare(a.lastSeen));

  function update(id, patch = {}) {
    const item = items.find((x) => x.id === id);
    if (!item) return { error: 'not_found' };
    if ('question' in patch) {
      const q = norm(patch.question).slice(0, 200);
      if (!q) return { error: 'invalid_question' };
      item.question = q;
    }
    if ('answer' in patch) {
      const a = norm(patch.answer).slice(0, 400);
      if (!a) return { error: 'invalid_answer' };
      Object.assign(item, { answer: a, teachIntent: null, status: 'answered' });
    } else if ('teachIntent' in patch) {
      if (!/^[a-z_]{2,40}$/.test(String(patch.teachIntent))) return { error: 'invalid_intent' };
      if (NOT_TEACHABLE.includes(patch.teachIntent)) return { error: 'intent_not_teachable' };
      Object.assign(item, { teachIntent: patch.teachIntent, answer: null, status: 'taught' });
    } else if (patch.status === 'ignored' || patch.status === 'new') {
      Object.assign(item, { status: patch.status, answer: null, teachIntent: null });
    } else if (!('question' in patch)) {
      return { error: 'invalid_update' };
    }
    save();
    return { item };
  }

  // What the bot reads at the start of each call: approved answers and extra phrases per intent.
  function knowledge() {
    const faqs = items.filter((x) => x.status === 'answered').map((x) => ({ id: x.id, question: x.question, answer: x.answer }));
    const intents = {};
    for (const x of items.filter((y) => y.status === 'taught' && !NOT_TEACHABLE.includes(y.teachIntent))) (intents[x.teachIntent] ??= []).push(x.question);
    return { faqs, intents };
  }

  return { capture, list, update, knowledge };
}
