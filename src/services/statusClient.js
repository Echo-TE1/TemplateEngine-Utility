'use strict';

const { normalize } = require('../lib/normalize');

/**
 * Downloads Roblox's public Status.io data and returns it in the normalized shape.
 * It can never wait longer than timeoutMs: the request is aborted, and a second timer
 * rejects even if the abort were ignored (so a command or check can never hang on this).
 */
async function fetchStatus(url, { timeoutMs = 10_000, log = console } = {}) {
  const started = Date.now();
  const controller = new AbortController();
  let abortTimer;
  let hardTimer;

  const work = (async () => {
    log.log('[Status] Requesting Roblox status…');
    const response = await fetch(url, {
      headers: { Accept: 'application/json', 'User-Agent': 'roblox-outage-tracker/1.0' },
      signal: controller.signal,
    });
    log.log(`[Status] HTTP ${response.status} after ${Date.now() - started}ms, reading body…`);
    if (!response.ok) {
      throw new Error(`Roblox status API returned HTTP ${response.status}.`);
    }
    const body = await response.json();
    log.log(`[Status] Done in ${Date.now() - started}ms.`);
    return normalize(body);
  })();
  work.catch(() => {}); // if the timeout wins, a late failure must not become an unhandled rejection

  const timeoutError = () => new Error(`No answer from Roblox's status API within ${timeoutMs / 1000}s.`);
  const hard = new Promise((_, reject) => {
    abortTimer = setTimeout(() => controller.abort(), timeoutMs);
    hardTimer = setTimeout(() => reject(timeoutError()), timeoutMs + 1000);
  });

  try {
    return await Promise.race([work, hard]);
  } catch (error) {
    if (error.name === 'AbortError' || error.cause?.name === 'AbortError') throw timeoutError();
    throw error;
  } finally {
    clearTimeout(abortTimer);
    clearTimeout(hardTimer);
  }
}

module.exports = { fetchStatus };
