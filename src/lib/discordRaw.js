'use strict';

// Direct calls to Discord's interaction webhook, used to deliver replies even if discord.js's own
// request queue is stuck. Uses Node's built-in fetch with a hard timeout. Interaction webhooks need no
// bot token: the interaction's own token is the credential, so it is never logged.

const API = 'https://discord.com/api/v10';

/** discord.js payload (builders, camelCase) -> the JSON body Discord expects. */
function toBody(payload) {
  const json = (x) => (x && typeof x.toJSON === 'function' ? x.toJSON() : x);
  const body = {};
  if (payload.content !== undefined) body.content = payload.content;
  if (payload.embeds) body.embeds = payload.embeds.map(json);
  if (payload.components) body.components = payload.components.map(json);
  if (payload.allowedMentions) body.allowed_mentions = payload.allowedMentions;
  if (payload.flags !== undefined) body.flags = payload.flags;
  if (payload.files && payload.files.length) body.attachments = payload.files.map((f, id) => ({ id, filename: f.name }));
  return body;
}

/** Files ({ attachment: Buffer, name }) go up as multipart form data; everything else is plain JSON. */
function encode(body, files) {
  if (!files || !files.length) return { headers: { 'Content-Type': 'application/json' }, payload: JSON.stringify(body) };
  const form = new FormData();
  form.append('payload_json', JSON.stringify(body));
  files.forEach((f, i) => form.append(`files[${i}]`, new Blob([f.attachment]), f.name));
  return { headers: {}, payload: form }; // fetch sets the multipart boundary itself
}

async function raw(method, path, body, { fetchImpl = fetch, timeoutMs = 8000, files } = {}) {
  const { headers, payload } = encode(body, files);
  const response = await fetchImpl(`${API}${path}`, {
    method,
    headers: { ...headers, 'User-Agent': 'mock-quote-bot/1.0' },
    body: payload,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (response.status >= 200 && response.status < 300) return;
  let detail = '';
  try {
    detail = String((await response.text()) || '').slice(0, 200);
  } catch {
    detail = '';
  }
  throw new Error(`Discord answered HTTP ${response.status} ${detail}`.trim());
}

module.exports = { toBody, raw, encode };
