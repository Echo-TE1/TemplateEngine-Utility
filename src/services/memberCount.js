'use strict';

const { startLoop } = require('../lib/loop');

const CUSTOM_STATUS = 4; // Discord activity type for a custom status
const MAX_STATE = 128; // Discord's limit for a custom status

/**
 * Keeps the bot's custom status (the note bubble next to its avatar) showing how many members a server has.
 * The count is read with one REST call, so no privileged intent is needed. The status is only changed
 * when the number changed, and checked every few minutes.
 */
class MemberCountNote {
  constructor({ client, guildId, everyMs = 10 * 60_000, log = console }) {
    Object.assign(this, { client, guildId, everyMs, log });
    this.loop = null;
    this.lastText = null;
    this.stats = { count: null, updatedAt: null, error: null };
  }

  static text(count) {
    return `${count.toLocaleString('en-US')} members`.slice(0, MAX_STATE);
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
      this.client.user.setPresence({ status: 'online', activities: [{ type: CUSTOM_STATUS, name: 'Custom Status', state: text }] });
      this.lastText = text;
      this.stats.updatedAt = new Date().toISOString();
      this.stats.error = null;
      this.log.log(`[MemberCount] Bot status set to "${text}"`);
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
