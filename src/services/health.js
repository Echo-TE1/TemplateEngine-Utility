'use strict';

const { request } = require('../lib/http');
const { startLoop } = require('../lib/loop');
const { evaluate, isOk, logFor } = require('../lib/health');
const { healthEmbed } = require('../embeds');

// Mongo field names cannot contain "." or "$".
const safeKey = (s) => String(s).replace(/[.$]/g, '_');

/**
 * Checks Roblox directly (website, login, public APIs) and posts to the Website Status / API Status
 * logs when a target stops answering or comes back. This can notice trouble before Roblox's own
 * status page does.
 */
class HealthMonitor {
  constructor({ store, config, notifier, requestFn = request, log = console }) {
    this.store = store;
    this.config = config;
    this.notifier = notifier;
    this.request = requestFn;
    this.log = log;
    this.state = {}; // key -> { status, fails, oks, downSince }
    this.latest = {}; // name -> { ok, httpStatus, ms, error, at } for /botstatus
    this.loop = null;
  }

  get targets() {
    return this.config.healthTargets;
  }

  async start() {
    const saved = await this.store.getDoc('health');
    this.state = saved.targets || {};
    this.loop = startLoop(() => this.runOnce(), this.config.healthIntervalSeconds * 1000, {
      name: 'Health',
      delayMs: 5000,
      log: this.log,
    });
    this.log.log(`[Health] Checking ${this.targets.map((t) => t.name).join(', ')} every ${this.config.healthIntervalSeconds}s.`);
  }

  stop() {
    if (this.loop) this.loop.stop();
  }

  async #check(target) {
    const at = new Date().toISOString();
    try {
      const result = await this.request(target.url, { timeoutMs: this.config.healthTimeoutSeconds * 1000 });
      return { ok: isOk(result.status), httpStatus: result.status, ms: result.ms, at };
    } catch (error) {
      return { ok: false, httpStatus: null, ms: null, error: error.message, at };
    }
  }

  async runOnce() {
    const results = await Promise.all(this.targets.map(async (t) => [t, await this.#check(t)]));
    const events = [];

    for (const [target, result] of results) {
      this.latest[target.name] = result;
      const key = safeKey(target.name);
      const { next, change } = evaluate(this.state[key], result.ok, this.config.healthFailThreshold, result.at);
      this.state[key] = next;
      if (change) events.push({ target, change, result, state: next });
    }

    // Save first, then announce: a crash in between can lose an alert but never repeat one.
    await this.store.setDoc('health', { targets: this.state });

    for (const event of events) {
      this.log.log(`[Health] ${event.target.name} is ${event.change === 'down' ? 'DOWN' : 'back up'}.`);
      await this.notifier.post(logFor(event.target.name), { embeds: [healthEmbed(event)] });
    }
  }
}

module.exports = { HealthMonitor };
