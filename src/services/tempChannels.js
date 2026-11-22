'use strict';

const { startLoop } = require('../lib/loop');

const DOC = 'tempchannels';

/** Temporary channels from /channel create. Deadlines are saved, so they survive restarts (with MongoDB). */
class TempChannels {
  constructor({ client, store, log = console, everyMs = 30_000 }) {
    Object.assign(this, { client, store, log, everyMs });
    this.items = {}; // channelId -> { guildId, name, deleteAt (ms) }
    this.loop = null;
  }

  get persistent() {
    return Boolean(this.store.persistent);
  }

  async load() {
    this.items = ((await this.store.getDoc(DOC)) || {}).items || {};
  }

  async #save() {
    await this.store.setDoc(DOC, { items: this.items });
  }

  async add(channel, deleteAt) {
    this.items[channel.id] = { guildId: channel.guildId || null, name: channel.name, deleteAt };
    try {
      await this.#save();
    } catch (error) {
      delete this.items[channel.id];
      throw error;
    }
  }

  start() {
    this.loop = startLoop(() => this.check(), this.everyMs, { name: 'TempChannels', delayMs: 5_000, log: this.log });
  }

  stop() {
    if (this.loop) this.loop.stop();
  }

  /** Deletes every channel whose time is up. The record is removed in every case, so it never loops forever. */
  async check(now = Date.now()) {
    const due = Object.entries(this.items).filter(([, item]) => item.deleteAt <= now);
    if (!due.length) return 0;
    for (const [id] of due) {
      try {
        const channel = this.client.channels.cache.get(id) || (await this.client.channels.fetch(id).catch(() => null));
        if (channel) {
          await channel.delete('Temporary channel expired');
          this.log.log(`[TempChannels] Deleted expired channel ${id}`);
        }
      } catch (error) {
        this.log.warn(`[TempChannels] Could not delete channel ${id}: ${error.message}`);
      }
      delete this.items[id];
    }
    await this.#save();
    return due.length;
  }
}

module.exports = { TempChannels };
