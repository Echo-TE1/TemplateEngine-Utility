'use strict';

const { call } = require('../lib/robloxApi');
const { startLoop } = require('../lib/loop');
const { parseItems, diffMarket, searchItems, priceHistory } = require('../lib/economy');
const embeds = require('../embeds');

const DOC = 'economy';
const URL = 'https://www.rolimons.com/itemapi/itemdetails';
const MAX_POSTED = 10;

/**
 * Watches the Roblox limited-item market (RAP and value for every limited, from Rolimons' public list)
 * and posts big moves and new limiteds to the Economy log. The first read only becomes the baseline.
 * State is saved before anything is announced, so a crash can lose an alert but never repeat one.
 */
class EconomyTracker {
  constructor({ store, config, notifier, fetchImpl = fetch, log = console, now = Date.now }) {
    Object.assign(this, { store, config, notifier, fetchImpl, log, now });
    this.items = new Map();
    this.updatedAt = null;
    this.lastMovers = { at: null, movers: [], newItems: [] };
    this.status = { ok: null, error: null };
    this.resaleCache = new Map(); // id -> { at, data }
    this.loop = null;
  }

  async start() {
    const doc = (await this.store.getDoc(DOC)) || {};
    for (const [id, [name, rap, value, acronym]] of Object.entries(doc.items || {})) {
      this.items.set(id, { id, name, rap, value, acronym: acronym || '' });
    }
    this.updatedAt = doc.updatedAt || null;
    this.lastMovers = doc.lastMovers || this.lastMovers;
    this.loop = startLoop(() => this.runOnce(), this.config.economyPollMinutes * 60_000, { name: 'Economy', delayMs: 15_000, log: this.log });
    this.log.log(`[Economy] Watching ${this.items.size || 'the'} limited items every ${this.config.economyPollMinutes} min.`);
  }

  stop() {
    if (this.loop) this.loop.stop();
  }

  async #save() {
    const items = {};
    for (const [id, i] of this.items) items[id] = [i.name, i.rap, i.value, i.acronym];
    await this.store.setDoc(DOC, { items, updatedAt: this.updatedAt, lastMovers: this.lastMovers });
  }

  async runOnce() {
    let next;
    try {
      const response = await call(URL, { timeoutMs: 20_000, fetchImpl: this.fetchImpl });
      if (response.status === 429) throw new Error('Rolimons is rate limiting the bot');
      if (response.status !== 200) throw new Error(`HTTP ${response.status}`);
      next = parseItems(response.json);
    } catch (error) {
      if (this.status.ok !== false) this.log.warn(`[Economy] Could not read the item list: ${error.message}`);
      this.status = { ok: false, error: error.message };
      return;
    }
    this.status = { ok: true, error: null };

    const prev = this.items;
    const diff = prev.size ? diffMarket(prev, next, this.config.economy) : { movers: [], newItems: [], skipped: false };
    if (diff.skipped) this.log.warn('[Economy] Too many items changed at once; treating it as bad data and not announcing.');

    this.items = next;
    this.updatedAt = new Date(this.now()).toISOString();
    if (diff.movers.length || diff.newItems.length) {
      this.lastMovers = { at: this.updatedAt, movers: diff.movers.slice(0, 25), newItems: diff.newItems.slice(0, 25) };
    }
    await this.#save();

    if (diff.movers.length || diff.newItems.length) {
      await this.notifier.post('economy_updates', {
        embeds: [embeds.marketEmbed({ movers: diff.movers, newItems: diff.newItems, max: MAX_POSTED, tracked: next.size })],
      });
      this.log.log(`[Economy] ${diff.movers.length} mover(s), ${diff.newItems.length} new item(s).`);
    }
  }

  search(query, limit) {
    return searchItems(this.items, query, limit);
  }

  /** Roblox's own sales data for one item (price history, stock, sales). Cached for 5 minutes. */
  async resale(id) {
    const cached = this.resaleCache.get(id);
    if (cached && this.now() - cached.at < 5 * 60_000) return cached.data;
    const response = await call(`https://economy.roblox.com/v1/assets/${encodeURIComponent(id)}/resale-data`, { fetchImpl: this.fetchImpl });
    if (response.status === 429) throw new Error('Roblox is rate limiting the bot right now. Try again in a minute.');
    if (response.status !== 200 || !response.json) throw new Error(`Roblox has no sales data for this item (HTTP ${response.status}).`);
    this.resaleCache.set(id, { at: this.now(), data: response.json });
    if (this.resaleCache.size > 100) this.resaleCache.delete(this.resaleCache.keys().next().value);
    return response.json;
  }

  async thumbnail(id) {
    try {
      const r = await call(`https://thumbnails.roblox.com/v1/assets?assetIds=${encodeURIComponent(id)}&size=420x420&format=Png`, { timeoutMs: 5000, fetchImpl: this.fetchImpl });
      const item = r.status === 200 && r.json && r.json.data && r.json.data[0];
      return item && item.state === 'Completed' && /^https:\/\//.test(item.imageUrl || '') ? item.imageUrl : null;
    } catch {
      return null;
    }
  }

  history(resale) {
    return priceHistory(resale);
  }
}

module.exports = { EconomyTracker };
