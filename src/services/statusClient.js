'use strict';

const { normalize } = require('../lib/normalize');

/** Downloads Roblox's public Status.io data and returns it in the normalized shape. */
async function fetchStatus(url, { timeoutMs = 10_000 } = {}) {
  const response = await fetch(url, {
    headers: { Accept: 'application/json', 'User-Agent': 'roblox-outage-tracker/1.0' },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) {
    throw new Error(`Roblox status API returned HTTP ${response.status}.`);
  }
  return normalize(await response.json());
}

module.exports = { fetchStatus };
