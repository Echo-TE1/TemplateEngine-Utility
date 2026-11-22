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

  // One attempt; a failure is tried once more after a short pause, so a single dropped request never counts.
  async #check(target) {
    let last;
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      const at = new Date().toISOString();
      try {
        const result = await this.request(target.url, { timeoutMs: this.config.healthTimeoutSeconds * 1000 });
        last = { ok: isOk(result.status), httpStatus: result.status, ms: result.ms, at };
      } catch (error) {
        last = { ok: false, httpStatus: null, ms: null, error: error.message, at };
      }
      if (last.ok) break;
      if (attempt === 1) await new Promise((resolve) => setTimeout(resolve, this.retryPauseMs ?? 2000));
    }
    return last;
  }

  // Can this host reach the internet at all? Checked against sites that are not Roblox.
  async #controlReachable() {
    const urls = this.config.healthControlUrls || [];
    if (!urls.length) return true;
    const answers = await Promise.all(
      urls.map((url) =>
        this.request(url, { timeoutMs: this.config.healthTimeoutSeconds * 1000 })
          .then((r) => isOk(r.status))
          .catch(() => false),
      ),
    );
    return answers.some(Boolean);
  }

  async runOnce() {
    const results = await Promise.all(this.targets.map(async (t) => [t, await this.#check(t)]));
    const events = [];

    // If every failure is also a failure for non-Roblox sites, the bot's own connection is the problem.
    // Say nothing about Roblox in that case: it would be a false alarm.
    if (results.some(([, r]) => !r.ok) && !(await this.#controlReachable())) {
      this.log.warn('[Health] This host could not reach other sites either, so these results are ignored (not a Roblox problem).');
      for (const [target, result] of results) this.latest[target.name] = { ...result, inconclusive: true };
      return;
    }

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
