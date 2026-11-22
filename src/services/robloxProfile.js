'use strict';

const { call } = require('../lib/robloxApi');

const USERNAME = /^[A-Za-z0-9_]{3,20}$/;
const PRESENCE = ['Offline', 'Online', 'In game', 'In Studio'];
const CACHE_MS = 30_000;
const NAME_CACHE_MS = 10 * 60_000;
const MAX_CACHE = 200;

/** A lookup problem that is safe to show to the user. kind: invalid | notfound | ratelimited | error */
class ProfileError extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}

/**
 * Fetches a public Roblox profile. Every extra section (avatar, counts, badges, Rolimons, presence)
 * is optional: if it fails it is left null, so the embed shows "Unavailable" instead of a made-up value.
 */
class RobloxProfiles {
  constructor({ cookie = '', fetchImpl = fetch, log = console, now = Date.now } = {}) {
    this.cookie = cookie;
    this.fetchImpl = fetchImpl;
    this.log = log;
    this.now = now;
    this.profiles = new Map(); // id -> { at, profile }
    this.names = new Map(); // lowercase name -> { at, id }
    this.csrf = null;
  }

  static validUsername(text) {
    const name = String(text || '').trim();
    if (!USERNAME.test(name)) return null;
    if (name.startsWith('_') || name.endsWith('_') || (name.match(/_/g) || []).length > 1) return null;
    return name;
  }

  async #json(url, options = {}) {
    return call(url, { ...options, fetchImpl: this.fetchImpl });
  }

  #fail(response, what) {
    if (response.status === 429) throw new ProfileError('ratelimited', 'Roblox is rate limiting the bot right now. Try again in a minute.');
    throw new ProfileError('error', `Roblox did not answer correctly while ${what} (HTTP ${response.status}).`);
  }

  /** Username -> user id. */
  async resolveName(text) {
    const name = RobloxProfiles.validUsername(text);
    if (!name) throw new ProfileError('invalid', 'Roblox usernames are 3-20 letters, numbers or one underscore (not at the start or end).');
    const cached = this.names.get(name.toLowerCase());
    if (cached && this.now() - cached.at < NAME_CACHE_MS) return cached.id;

    let response;
    try {
      response = await this.#json('https://users.roblox.com/v1/usernames/users', {
        method: 'POST',
        body: { usernames: [name], excludeBannedUsers: false },
      });
    } catch (error) {
      throw new ProfileError('error', `Could not reach Roblox: ${error.message}`);
    }
    if (response.status !== 200) this.#fail(response, 'looking up the username');
    const hit = response.json && Array.isArray(response.json.data) ? response.json.data[0] : null;
    if (!hit || !hit.id) throw new ProfileError('notfound', `No Roblox account is named \`${name}\`.`);
    this.names.set(name.toLowerCase(), { at: this.now(), id: String(hit.id) });
    return String(hit.id);
  }

  /** Full profile for a user id. `force` skips the short cache (used by Refresh). */
  async byId(id, { force = false } = {}) {
    const cached = this.profiles.get(String(id));
    const age = cached ? this.now() - cached.at : Infinity;
    if (cached && age < (force ? 5_000 : CACHE_MS)) return cached.profile;

    let core;
    try {
      core = await this.#json(`https://users.roblox.com/v1/users/${encodeURIComponent(id)}`);
    } catch (error) {
      throw new ProfileError('error', `Could not reach Roblox: ${error.message}`);
    }
    if (core.status === 404 || core.status === 400) throw new ProfileError('notfound', 'That Roblox account no longer exists.');
    if (core.status !== 200 || !core.json || !core.json.id) this.#fail(core, 'loading the profile');
    const user = core.json;

    const flags = { rateLimited: false };
    const [avatar, followers, following, friends, badges, rolimons, presence] = await Promise.all([
      this.#avatar(user.id, flags),
      this.#count(user.id, 'followers', flags),
      this.#count(user.id, 'followings', flags),
      this.#count(user.id, 'friends', flags),
      this.#badges(user.id, flags),
      this.#rolimons(user.id, flags),
      this.#presence(user.id, flags),
    ]);

    const profile = {
      id: String(user.id),
      name: user.name,
      displayName: user.displayName || user.name,
      verified: Boolean(user.hasVerifiedBadge),
      banned: Boolean(user.isBanned),
      description: typeof user.description === 'string' ? user.description : '',
      created: user.created || null,
      avatarUrl: avatar,
      followers,
      following,
      friends,
      badges,
      rolimons,
      presence,
      rateLimited: flags.rateLimited,
      fetchedAt: new Date(this.now()).toISOString(),
      url: `https://www.roblox.com/users/${user.id}/profile`,
    };
    this.profiles.set(profile.id, { at: this.now(), profile });
    if (this.profiles.size > MAX_CACHE) this.profiles.delete(this.profiles.keys().next().value);
    return profile;
  }

  async lookup(username) {
    return this.byId(await this.resolveName(username));
  }

  // ---- optional sections: null when unavailable ----------------------------------------
  async #try(flags, url, options) {
    try {
      const response = await this.#json(url, options);
      if (response.status === 429) flags.rateLimited = true;
      return response.status === 200 ? response : null;
    } catch (error) {
      this.log.warn(`[Profile] ${url.split('?')[0]} failed: ${error.message}`);
      return null;
    }
  }

  async #avatar(id, flags) {
    const r = await this.#try(flags, `https://thumbnails.roblox.com/v1/users/avatar?userIds=${id}&size=420x420&format=Png&isCircular=false`);
    const item = r && r.json && r.json.data && r.json.data[0];
    return item && item.state === 'Completed' && /^https:\/\//.test(item.imageUrl || '') ? item.imageUrl : null;
  }

  async #count(id, kind, flags) {
    const r = await this.#try(flags, `https://friends.roblox.com/v1/users/${id}/${kind}/count`);
    return r && r.json && Number.isFinite(r.json.count) ? r.json.count : null;
  }

  // Roblox's own account badges (Administrator, Veteran, ...), not game badges.
  async #badges(id, flags) {
    const r = await this.#try(flags, `https://accountinformation.roblox.com/v1/users/${id}/roblox-badges`);
    if (!r || !Array.isArray(r.json)) return null;
    return r.json
      .filter((b) => b && b.name)
      .map((b) => ({ name: String(b.name), imageUrl: /^https:\/\//.test(b.imageUrl || '') ? b.imageUrl : null }));
  }

  // RAP and item value come from Rolimons (Roblox has no public API for them). Null if not tracked there.
  async #rolimons(id, flags) {
    const r = await this.#try(flags, `https://api.rolimons.com/players/v1/playerinfo/${id}`);
    const d = r && r.json;
    if (!d || d.success !== true) return null;
    const num = (v) => (Number.isFinite(v) ? v : null);
    return {
      private: Boolean(d.privacy_enabled),
      rap: d.privacy_enabled ? null : num(d.rap),
      value: d.privacy_enabled ? null : num(d.value),
      rank: num(d.rank),
      lastOnline: Number.isFinite(d.last_online) && d.last_online > 0 ? new Date(d.last_online * 1000).toISOString() : null,
      updatedAt: Number.isFinite(d.stats_updated) ? new Date(d.stats_updated * 1000).toISOString() : null,
    };
  }

  // Roblox only shows presence to logged-in requests, so this needs the optional ROBLOX_COOKIE.
  async #presence(id, flags) {
    if (!this.cookie) return null;
    const send = (csrf) =>
      this.#try(flags, 'https://presence.roblox.com/v1/presence/users', {
        method: 'POST',
        body: { userIds: [Number(id)] },
        headers: { Cookie: `.ROBLOSECURITY=${this.cookie}`, ...(csrf ? { 'x-csrf-token': csrf } : {}) },
      });
    try {
      let response = await this.#json('https://presence.roblox.com/v1/presence/users', {
        method: 'POST',
        body: { userIds: [Number(id)] },
        headers: { Cookie: `.ROBLOSECURITY=${this.cookie}`, ...(this.csrf ? { 'x-csrf-token': this.csrf } : {}) },
      });
      if (response.status === 403 && response.csrf) {
        this.csrf = response.csrf; // Roblox asks for a token on the first POST; retry once with it
        response = await send(this.csrf);
      } else if (response.status === 429) {
        flags.rateLimited = true;
        return null;
      } else if (response.status !== 200) {
        return null;
      }
      const p = response && response.json && response.json.userPresences && response.json.userPresences[0];
      if (!p) return null;
      return {
        status: PRESENCE[p.userPresenceType] || 'Unknown',
        online: p.userPresenceType > 0,
        lastOnline: p.lastOnline || null,
      };
    } catch (error) {
      this.log.warn(`[Profile] presence failed: ${error.message}`);
      return null;
    }
  }
}

module.exports = { RobloxProfiles, ProfileError };
