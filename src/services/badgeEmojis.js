'use strict';

const NAME_PREFIX = 'rb_';
const MAX_BYTES = 256 * 1024; // Discord's emoji size limit
const RETRY_FAILED_AFTER_MS = 10 * 60_000;
const MAX_NEW_PER_LOOKUP = 15;
const TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

/**
 * Roblox badge icons as the bot's own emoji ("application emojis"), so they show inline in the profile card.
 * Each badge is uploaded once (named rb_<badge id>) and reused forever; the existing ones are read back
 * from Discord on start, so nothing needs to be stored. If an upload fails the card shows the badge name.
 * Also picks up optional emoji you upload yourself, named "verified" and "premium".
 */
class BadgeEmojis {
  constructor({ client, fetchImpl = fetch, log = console, now = Date.now }) {
    Object.assign(this, { client, fetchImpl, log, now });
    this.known = null; // name -> markup like <:rb_1:123>
    this.failed = new Map(); // name -> time of last failure
    this.inflight = new Map(); // name -> Promise
  }

  get #base() {
    return `/applications/${this.client.application.id}/emojis`;
  }

  #markup(e) {
    return `<${e.animated ? 'a' : ''}:${e.name}:${e.id}>`;
  }

  async #load() {
    if (this.known) return;
    this.known = new Map();
    try {
      const res = await this.client.rest.get(this.#base);
      for (const e of (res && res.items) || []) this.known.set(e.name, this.#markup(e));
    } catch (error) {
      this.known = null; // try again next time
      throw error;
    }
  }

  async #download(url) {
    if (!/^https:\/\//.test(url || '')) throw new Error('no icon url');
    const response = await this.fetchImpl(url, { signal: AbortSignal.timeout(8000) });
    if (response.status !== 200) throw new Error(`icon HTTP ${response.status}`);
    const type = String(response.headers.get('content-type') || 'image/png').split(';')[0].trim().toLowerCase();
    const bytes = Buffer.from(await response.arrayBuffer());
    if (!TYPES.has(type)) throw new Error(`icon type ${type}`);
    if (!bytes.length || bytes.length > MAX_BYTES) throw new Error(`icon size ${bytes.length}`);
    return `data:${type};base64,${bytes.toString('base64')}`;
  }

  async #upload(name, url) {
    const image = await this.#download(url);
    const e = await this.client.rest.post(this.#base, { body: { name, image } });
    const markup = this.#markup(e);
    this.known.set(name, markup);
    return markup;
  }

  #ensure(name, url) {
    if (this.known.has(name)) return Promise.resolve(this.known.get(name));
    const failedAt = this.failed.get(name);
    if (failedAt && this.now() - failedAt < RETRY_FAILED_AFTER_MS) return Promise.resolve(null);
    if (!this.inflight.has(name)) {
      const job = this.#upload(name, url)
        .catch((error) => {
          this.failed.set(name, this.now());
          this.log.warn(`[BadgeEmoji] ${name}: ${error.message}`);
          return null;
        })
        .finally(() => this.inflight.delete(name));
      this.inflight.set(name, job);
    }
    return this.inflight.get(name);
  }

  /**
   * @returns {Promise<{ verified: string|null, premium: string|null, badges: Array<string|null> }>}
   * Never throws: on any problem the card simply falls back to text.
   */
  async forProfile(profile) {
    const empty = { verified: null, premium: null, badges: (profile.badges || []).map(() => null) };
    try {
      await this.#load();
      let budget = MAX_NEW_PER_LOOKUP;
      const badges = [];
      const jobs = (profile.badges || []).map((b) => {
        if (!b.id) return Promise.resolve(null);
        const name = `${NAME_PREFIX}${b.id}`;
        if (!this.known.has(name)) {
          if (budget <= 0) return Promise.resolve(null);
          budget -= 1;
        }
        return this.#ensure(name, b.imageUrl);
      });
      badges.push(...(await Promise.all(jobs)));
      return { verified: this.known.get('verified') || null, premium: this.known.get('premium') || null, badges };
    } catch (error) {
      this.log.warn(`[BadgeEmoji] using text badges: ${error.message}`);
      return empty;
    }
  }
}

module.exports = { BadgeEmojis };
