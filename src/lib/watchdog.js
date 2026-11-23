'use strict';

/**
 * Notices when the bot has been disconnected from Discord for too long and asks for a restart
 * (the host starts it again). Pure logic: the caller supplies isReady() and onStuck().
 */
function startWatchdog({ isReady, onStuck, maxDownMs = 5 * 60_000, everyMs = 30_000, now = Date.now, log = console }) {
  let downSince = null;
  const timer = setInterval(() => {
    if (isReady()) {
      if (downSince) log.log(`[Watchdog] Back online after ${Math.round((now() - downSince) / 1000)}s.`);
      downSince = null;
      return;
    }
    if (downSince === null) {
      downSince = now();
      log.warn('[Watchdog] Not connected to Discord.');
      return;
    }
    if (now() - downSince >= maxDownMs) {
      log.error(`[Watchdog] Offline for ${Math.round((now() - downSince) / 1000)}s. Restarting.`);
      onStuck();
    }
  }, everyMs);
  timer.unref();
  return { stop: () => clearInterval(timer) };
}

module.exports = { startWatchdog };
