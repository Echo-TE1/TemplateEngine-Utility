'use strict';

/**
 * Runs fn now (after delayMs) and then again every intervalMs, never overlapping, never crashing.
 * Returns { stop() }.
 */
function startLoop(fn, intervalMs, { name = 'loop', delayMs = 0, log = console } = {}) {
  let stopped = false;
  let timer = null;

  const tick = async () => {
    try {
      await fn();
    } catch (error) {
      log.error(`[${name}] ${error.stack || error.message}`);
    }
    if (!stopped) timer = setTimeout(tick, intervalMs);
  };
  timer = setTimeout(tick, delayMs);

  return {
    stop() {
      stopped = true;
      clearTimeout(timer);
    },
  };
}

module.exports = { startLoop };
