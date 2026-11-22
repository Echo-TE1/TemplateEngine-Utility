'use strict';

/**
 * One JSON request to a Roblox (or partner) API that can never hang.
 * Returns { status, json, retryAfter }. Network problems and timeouts throw.
 * Never log `headers`: they may carry a login cookie.
 */
async function call(url, { method = 'GET', body, headers = {}, timeoutMs = 8000, fetchImpl = fetch } = {}) {
  const controller = new AbortController();
  let hardTimer;
  const hard = new Promise((_, reject) => {
    hardTimer = setTimeout(() => {
      controller.abort();
      reject(new Error(`Timed out after ${timeoutMs / 1000}s`));
    }, timeoutMs + 500);
  });
  const work = (async () => {
    const response = await fetchImpl(url, {
      method,
      headers: {
        'User-Agent': 'roblox-outage-tracker/1.0',
        Accept: 'application/json',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    let json = null;
    try {
      json = await response.json();
    } catch {
      json = null;
    }
    return {
      status: response.status,
      json,
      retryAfter: Number.parseInt(response.headers.get('retry-after') || '', 10) || null,
      csrf: response.headers.get('x-csrf-token') || null,
    };
  })();
  try {
    return await Promise.race([work, hard]);
  } finally {
    clearTimeout(hardTimer);
  }
}

module.exports = { call };
