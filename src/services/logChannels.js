'use strict';

// The five log categories the owner can point at a channel with /setup log.
const LOG_TYPES = [
  { value: 'platform_status', name: 'Platform Status' },
  { value: 'website_status', name: 'Website Status' },
  { value: 'api_status', name: 'API Status' },
  { value: 'game_updates', name: 'Game Updates' },
  { value: 'bot_updates', name: 'Bot Updates' },
];

// What each log receives (shown by /setup view).
const LOG_DESCRIPTIONS = {
  platform_status: 'Live Roblox status message + incident alerts',
  website_status: 'Website outages: Roblox status "Website" + direct roblox.com checks',
  api_status: 'API / services outages: other Roblox services + direct API checks',
  game_updates: 'Roblox news: platform updates, events and announcements',
  bot_updates: 'Bot startup, problems, and the daily backup',
};

const labelFor = (type) => (LOG_TYPES.find((t) => t.value === type) || {}).name || null;

/**
 * Remembers which channel each log category posts to (saved in the store, so it survives restarts).
 * Any part of the bot can post to a category with: await logChannels.send('bot_updates', { embeds: [...] })
 */
class LogChannels {
  constructor({ client, store }) {
    this.client = client;
    this.store = store;
    this.map = {};
  }

  async load() {
    this.map = (await this.store.getLogChannels()) || {};
  }

  /** The saved record ({ channelId, guildId, setBy, setAt }) or null. */
  entry(type) {
    return this.map[type] || null;
  }

  get(type) {
    const entry = this.entry(type);
    return entry ? entry.channelId : null;
  }

  async set(type, record) {
    await this.store.setLogChannel(type, record); // save first: memory is only updated if saving worked
    this.map[type] = record;
  }

  /** Stops posting to a category. Returns true if it was set. */
  async clear(type) {
    const had = Boolean(this.map[type]);
    await this.store.clearLogChannel(type);
    delete this.map[type];
    return had;
  }

  /** Posts to a category's channel. Returns the message, or null if that category is not set up. */
  async send(type, payload) {
    const id = this.get(type);
    if (!id) return null;
    const channel = this.client.channels.cache.get(id) || (await this.client.channels.fetch(id));
    if (!channel || !channel.isTextBased()) throw new Error(`Channel ${id} is missing or is not a text channel.`);
    return channel.send(payload);
  }
}

module.exports = { LogChannels, LOG_TYPES, LOG_DESCRIPTIONS, labelFor };
