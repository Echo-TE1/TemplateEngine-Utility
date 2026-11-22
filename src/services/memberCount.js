'use strict';

const { startLoop } = require('../lib/loop');

const MAX_ABOUT = 400; // Discord's limit for the bot's "About Me"

/**
 * Keeps the bot's "About Me" (its profile note) showing how many members a server has.
 * Uses plain REST calls, so no privileged intent is needed. It only edits when the number changed,
 * and checks every few minutes, because Discord rate limits profile edits.
 */
class MemberCountNote {
  constructor({ client, guildId, everyMs = 10 * 60_000, log = console }) {
    Object.assign(this, { client, guildId, everyMs, log });
    this.loop = null;
    this.lastText = null;
    this.stats = { count: null, updatedAt: null, error: null };
  }

  static text(count) {
    return `Watching over ${count.toLocaleString('en-US')} members`.slice(0, MAX_ABOUT);
  }

  async #count() {
    const guild = await this.client.rest.get(`/guilds/${this.guildId}`, { query: new URLSearchParams({ with_counts: 'true' }) });
    const n = guild && guild.approximate_member_count;
    if (!Number.isInteger(n) || n < 0) throw new Error('Discord did not return a member count');
    return n;
  }

  /** One check. Returns true if the note was changed. Never throws. */
  async update() {
    try {
      const count = await this.#count();
      this.stats.count = count;
      const text = MemberCountNote.text(count);
      if (text === this.lastText) {
        this.stats.error = null;
        return false;
      }
      await this.client.rest.patch('/applications/@me', { body: { description: text } });
      this.lastText = text;
      this.stats.updatedAt = new Date().toISOString();
      this.stats.error = null;
      this.log.log(`[MemberCount] Bot note set to "${text}"`);
      return true;
    } catch (error) {
      const hint = error.status === 404 || error.code === 10004 || error.code === 50001 ? ' (is the bot in that server?)' : '';
      this.stats.error = `${error.message}${hint}`;
      this.log.warn(`[MemberCount] ${this.stats.error}`);
      return false;
    }
  }

  start() {
    this.loop = startLoop(() => this.update(), this.everyMs, { name: 'MemberCount', delayMs: 3_000, log: this.log });
  }

  stop() {
    if (this.loop) this.loop.stop();
  }
}

module.exports = { MemberCountNote };
