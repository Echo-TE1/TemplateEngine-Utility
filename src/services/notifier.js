'use strict';

const { startLoop } = require('../lib/loop');

const MAX_ATTEMPTS = 3;
const MAX_QUEUE = 50;

/**
 * Posts to the /setup log channels and never throws. A post that fails (Discord hiccup, missing
 * permission) is retried a few times in the background, then dropped with a log line.
 */
class Notifier {
  constructor({ logChannels, log = console, retryEveryMs = 60_000 }) {
    this.logChannels = logChannels;
    this.log = log;
    this.retryEveryMs = retryEveryMs;
    this.queue = [];
    this.loop = null;
  }

  start() {
    this.loop = startLoop(() => this.flush(), this.retryEveryMs, {
      name: 'Notifier',
      delayMs: this.retryEveryMs,
      log: this.log,
    });
  }

  stop() {
    if (this.loop) this.loop.stop();
  }

  /** @returns {Promise<boolean>} true if it was posted (false: not set up, or queued for retry) */
  async post(type, payload) {
    if (!this.logChannels.get(type)) return false; // that log is not set up: nothing to do
    return this.#attempt({ type, payload: this.#safe(payload), attempts: 0 });
  }

  // No pings unless the caller asked for them explicitly.
  #safe(payload) {
    return payload.allowedMentions ? payload : { ...payload, allowedMentions: { parse: [] } };
  }

  async #attempt(item) {
    try {
      await this.logChannels.send(item.type, item.payload);
      return true;
    } catch (error) {
      item.attempts += 1;
      this.log.warn(`[Notifier] Could not post to ${item.type} (try ${item.attempts}): ${error.message}`);
      if (item.attempts < MAX_ATTEMPTS && this.queue.length < MAX_QUEUE) this.queue.push(item);
      return false;
    }
  }

  async flush() {
    const items = this.queue.splice(0);
    for (const item of items) await this.#attempt(item);
  }
}

module.exports = { Notifier };
