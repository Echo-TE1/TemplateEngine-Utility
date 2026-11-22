'use strict';

/**
 * One small GET request that can never hang: it is aborted after timeoutMs, and a second timer
 * rejects even if the abort were ignored.
 *
 * Returns { status, ms, headers: { etag, lastModified }, json?, text? }.
 *  - default: the body is discarded without downloading it (cheap health checks)
 *  - readJson: parse the body as JSON
 *  - readText: read at most maxBytes of the body as text (feeds list newest items first, so a
 *    cut-off tail is harmless and bandwidth stays small)
 * Network problems and timeouts throw.
 */
async function request(url, { timeoutMs = 8000, readJson = false, readText = false, maxBytes = 200_000, redirect = 'manual', headers = {} } = {}) {
  const started = Date.now();
  const controller = new AbortController();
  let abortTimer;
  let hardTimer;

  const work = (async () => {
    const response = await fetch(url, {
      method: 'GET',
      redirect, // "manual": a redirect (e.g. to a login page) still proves the server answered
      headers: { 'User-Agent': 'roblox-outage-tracker/1.0', Accept: '*/*', ...headers },
      signal: controller.signal,
    });
    const result = {
      status: response.status,
      ms: Date.now() - started,
      headers: {
        etag: response.headers.get('etag') || null,
        lastModified: response.headers.get('last-modified') || null,
      },
    };

    if (readJson) {
      result.json = await response.json();
    } else if (readText && response.status === 200 && response.body) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let text = '';
      let bytes = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        text += decoder.decode(value, { stream: true });
        if (bytes >= maxBytes) {
          result.truncated = true;
          await reader.cancel().catch(() => {});
          break;
        }
      }
      result.text = text;
      result.bytes = bytes;
    } else if (response.body) {
      await response.body.cancel().catch(() => {});
    }
    result.ms = Date.now() - started;
    return result;
  })();
  work.catch(() => {}); // a late failure after the timeout won must not become an unhandled rejection

  const timeoutError = () => new Error(`No answer within ${timeoutMs / 1000}s`);
  const hard = new Promise((_, reject) => {
    abortTimer = setTimeout(() => controller.abort(), timeoutMs);
    hardTimer = setTimeout(() => reject(timeoutError()), timeoutMs + 1000);
  });

  try {
    return await Promise.race([work, hard]);
  } catch (error) {
    if (error.name === 'AbortError' || error.cause?.name === 'AbortError') throw timeoutError();
    const cause = error.cause && (error.cause.code || error.cause.message);
    throw new Error(cause ? `${error.message} (${cause})` : error.message);
  } finally {
    clearTimeout(abortTimer);
    clearTimeout(hardTimer);
  }
}

module.exports = { request };
