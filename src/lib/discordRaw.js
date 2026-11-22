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
  return body;
}

async function raw(method, path, body, { fetchImpl = fetch, timeoutMs = 8000 } = {}) {
  const response = await fetchImpl(`${API}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'User-Agent': 'roblox-outage-tracker/1.0' },
    body: JSON.stringify(body),
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

module.exports = { toBody, raw };
