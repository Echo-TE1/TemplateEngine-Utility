'use strict';

const { request } = require('../lib/http');
const { startLoop } = require('../lib/loop');
const { parseFeed, findNew } = require('../lib/feed');
const { newsEmbed } = require('../embeds');

const safeKey = (s) => String(s).replace(/[.$]/g, '_');

/**
 * Watches Roblox's own news feeds (platform updates, events, announcements) and posts new items to
 * the Game Updates log. Cheap by design: conditional requests (an unchanged feed costs a tiny
 * "not modified" answer), only the first ~150 KB of a feed is read, and the first read of a feed
 * only remembers what is already there instead of flooding the channel.
 */
class NewsWatcher {
  constructor({ store, config, notifier, requestFn = request, log = console }) {
    this.store = store;
    this.config = config;
    this.notifier = notifier;
    this.request = requestFn;
    this.log = log;
    this.state = {}; // feedKey -> { seen: [ids], etag, lastModified }
    this.status = {}; // feed name -> { ok, at, error, items } for /botstatus
    this.loop = null;
  }

  get feeds() {
    return this.config.newsFeeds;
  }

  async start() {
    this.state = (await this.store.getDoc('news')).feeds || {};
    this.loop = startLoop(() => this.runOnce(), this.config.newsPollMinutes * 60_000, {
      name: 'News',
      delayMs: 10_000,
      log: this.log,
    });
    this.log.log(`[News] Watching ${this.feeds.map((f) => f.name).join(', ')} every ${this.config.newsPollMinutes} min.`);
  }

  stop() {
    if (this.loop) this.loop.stop();
  }

  async #checkFeed(feed) {
    const key = safeKey(feed.name);
    const prev = this.state[key] || { seen: [], etag: null, lastModified: null, initialized: false };
    const headers = {};
    if (prev.etag) headers['If-None-Match'] = prev.etag;
    if (prev.lastModified) headers['If-Modified-Since'] = prev.lastModified;

    const response = await this.request(feed.url, {
      readText: true,
      maxBytes: 150_000,
      timeoutMs: 15_000,
      redirect: 'follow',
      headers: { Accept: 'application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5', ...headers },
    });

    if (response.status === 304) {
      this.status[feed.name] = { ok: true, at: new Date().toISOString(), items: prev.seen.length, note: 'not modified' };
      return { fresh: [], state: prev };
    }
    if (response.status !== 200) throw new Error(`HTTP ${response.status}`);

    const items = parseFeed(response.text);
    if (!items.length) throw new Error('no items found in the feed (wrong URL, or not an RSS/Atom feed?)');

    const { fresh, seen } = findNew(prev.seen, items, { baseline: !prev.initialized, maxPost: 5 });
    this.status[feed.name] = { ok: true, at: new Date().toISOString(), items: items.length, bytes: response.bytes };
    return {
      fresh,
      state: { seen, initialized: true, etag: response.headers.etag, lastModified: response.headers.lastModified },
    };
  }

  async runOnce() {
    const toPost = [];

    for (const feed of this.feeds) {
      try {
        const { fresh, state } = await this.#checkFeed(feed);
        this.state[safeKey(feed.name)] = state;
        for (const item of fresh) toPost.push({ feed, item });
      } catch (error) {
        const before = this.status[feed.name];
        this.status[feed.name] = { ok: false, at: new Date().toISOString(), error: error.message };
        if (!before || before.ok) this.log.warn(`[News] Could not read "${feed.name}" (${feed.url}): ${error.message}`); // log once per outage
      }
    }

    // Save first, then announce: a crash in between can lose a post but never repeat one.
    await this.store.setDoc('news', { feeds: this.state });

    for (const { feed, item } of toPost) {
      await this.notifier.post('game_updates', { embeds: [newsEmbed(feed, item)] });
    }
    if (toPost.length) this.log.log(`[News] Posted ${toPost.length} new item(s).`);
  }
}

module.exports = { NewsWatcher };
