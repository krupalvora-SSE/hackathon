const REQUIRED = ['EXOTEL_API_KEY', 'EXOTEL_API_TOKEN', 'EXOTEL_ACCOUNT_SID', 'EXOTEL_CALLER_ID', 'EXOTEL_FLOW_ID'];

export const missingConfig = (env = process.env) => REQUIRED.filter((k) => !env[k]);

// Places an outbound call that connects the customer to the Voicebot flow. Returns the Exotel CallSid.
export async function connectCall({ to, customField, statusCallback }, { env = process.env, fetch = globalThis.fetch } = {}) {
  const missing = missingConfig(env);
  if (missing.length) return { error: 'exotel_not_configured', missing };

  const sid = env.EXOTEL_ACCOUNT_SID;
  // Singapore cluster: api.exotel.com / my.exotel.com. Mumbai: api.in.exotel.com / my.in.exotel.com.
  const host = env.EXOTEL_SUBDOMAIN || 'api.exotel.com';
  const flowHost = host.replace(/^api\./, 'my.');
  const form = new URLSearchParams({
    From: to,
    CallerId: env.EXOTEL_CALLER_ID,
    Url: `http://${flowHost}/${sid}/exoml/start_voice/${env.EXOTEL_FLOW_ID}`,
    CustomField: customField,
  });
  if (statusCallback) form.set('StatusCallback', statusCallback);

  let res, body;
  try {
    res = await fetch(`https://${host}/v1/Accounts/${sid}/Calls/connect.json`, {
      method: 'POST',
      headers: {
        authorization: 'Basic ' + Buffer.from(`${env.EXOTEL_API_KEY}:${env.EXOTEL_API_TOKEN}`).toString('base64'),
        'content-type': 'application/x-www-form-urlencoded',
      },
      body: form,
    });
    body = await res.json().catch(() => ({}));
  } catch (e) {
    return { error: 'exotel_failed', message: e.message };
  }
  if (!res.ok) return { error: 'exotel_failed', status: res.status, message: body?.RestException?.Message ?? `HTTP ${res.status}` };
  return { callSid: body?.Call?.Sid ?? null };
}
