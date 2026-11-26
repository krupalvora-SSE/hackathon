import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectCall, missingConfig } from './exotel.js';

const env = { EXOTEL_API_KEY: 'k', EXOTEL_API_TOKEN: 't', EXOTEL_ACCOUNT_SID: 'acme', EXOTEL_CALLER_ID: '0800', EXOTEL_FLOW_ID: '42', EXOTEL_SUBDOMAIN: 'api.in.exotel.com' };

test('missing config is reported without calling Exotel', async () => {
  assert.deepEqual(missingConfig({ EXOTEL_API_KEY: 'k' }), ['EXOTEL_API_TOKEN', 'EXOTEL_ACCOUNT_SID', 'EXOTEL_CALLER_ID', 'EXOTEL_FLOW_ID']);
  const r = await connectCall({ to: '+91', customField: 'C' }, { env: {}, fetch: () => assert.fail('should not fetch') });
  assert.equal(r.error, 'exotel_not_configured');
});

test('connect request shape', async () => {
  let req;
  const fetch = async (url, init) => { req = { url, init }; return { ok: true, json: async () => ({ Call: { Sid: 'SID1' } }) }; };
  const r = await connectCall({ to: '+910000000001', customField: 'CALL-1' }, { env, fetch });
  assert.deepEqual(r, { callSid: 'SID1' });
  assert.equal(req.url, 'https://api.in.exotel.com/v1/Accounts/acme/Calls/connect.json');
  assert.equal(req.init.headers.authorization, 'Basic ' + Buffer.from('k:t').toString('base64'));
  const f = req.init.body;
  assert.equal(f.get('From'), '+910000000001');
  assert.equal(f.get('CallerId'), '0800');
  assert.equal(f.get('Url'), 'http://my.exotel.com/acme/exoml/start_voice/42');
  assert.equal(f.get('CustomField'), 'CALL-1');
});

test('exotel error surfaces message', async () => {
  const fetch = async () => ({ ok: false, status: 403, json: async () => ({ RestException: { Message: 'Not allowed' } }) });
  const r = await connectCall({ to: '+91', customField: 'C' }, { env, fetch });
  assert.equal(r.error, 'exotel_failed');
  assert.equal(r.message, 'Not allowed');
});
