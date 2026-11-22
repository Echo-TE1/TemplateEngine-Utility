'use strict';

const { mirrorEmbed } = require('../lib/mirrorText');

const DOC = 'mirror';
const MAX_LINKS = 500; // remembered source -> copy pairs
const CHECK = '✅';

/**
 * Copies messages from one user in one channel into the Bot Updates log.
 *  - new message  -> posted, then the original gets a ✅ reaction
 *  - edited       -> the copy is edited
 *  - deleted      -> the copy is deleted
 * Events are handled one at a time, in order, so an edit can never overtake the post it edits.
 */
class MessageMirror {
  constructor({ store, logChannels, config, log = console }) {
    this.store = store;
    this.logChannels = logChannels;
    this.channelId = config.mirrorChannelId;
    this.userId = config.mirrorUserId;
    this.log = log;
    this.links = {}; // sourceMessageId -> mirrored message id
    this.chain = Promise.resolve();
    this.stats = { mirrored: 0, edited: 0, deleted: 0, errors: 0, lastError: null, lastAt: null };
  }

  async load() {
    const doc = await this.store.getDoc(DOC);
    this.links = (doc && doc.links) || {};
  }

  async #save() {
    const ids = Object.keys(this.links);
    if (ids.length > MAX_LINKS) {
      for (const id of ids.slice(0, ids.length - MAX_LINKS)) delete this.links[id];
    }
    await this.store.setDoc(DOC, { links: this.links });
  }

  #queue(task, label) {
    this.chain = this.chain.then(task).catch((error) => {
      this.stats.errors += 1;
      this.stats.lastError = `${label}: ${error.message}`;
      this.log.error(`[Mirror] ${label} failed: ${error.message}`);
    });
    return this.chain;
  }

  #matches(message) {
    return (
      message &&
      message.channelId === this.channelId &&
      message.author &&
      message.author.id === this.userId
    );
  }

  /** The copy of a source message in the log channel, or null. */
  async #copyOf(sourceId) {
    const copyId = this.links[sourceId];
    if (!copyId) return null;
    const channelId = this.logChannels.get('bot_updates');
    if (!channelId) return null;
    const channel = this.logChannels.client.channels.cache.get(channelId) || (await this.logChannels.client.channels.fetch(channelId));
    if (!channel || !channel.isTextBased()) return null;
    try {
      return await channel.messages.fetch(copyId);
    } catch {
      return null;
    }
  }

  async #post(message) {
    if (!this.logChannels.get('bot_updates')) return; // Bot Updates not set: nothing to do, no ✅
    const sent = await this.logChannels.send('bot_updates', {
      embeds: [mirrorEmbed(message)],
      allowedMentions: { parse: [] },
    });
    if (!sent) return;
    this.links[message.id] = sent.id;
    this.stats.mirrored += 1;
    this.stats.lastAt = new Date().toISOString();
    await this.#save().catch((e) => this.log.error(`[Mirror] could not save links: ${e.message}`));
    await message.react(CHECK).catch((e) => this.log.warn(`[Mirror] could not react: ${e.message}`));
  }

  onCreate(message) {
    if (!this.#matches(message)) return Promise.resolve();
    return this.#queue(() => this.#post(message), `post ${message.id}`);
  }

  onUpdate(oldMessage, newMessage) {
    return this.#queue(async () => {
      let message = newMessage;
      if (message.partial) message = await message.fetch();
      if (!this.#matches(message)) return;
      if (oldMessage && !oldMessage.partial && oldMessage.content === message.content &&
          oldMessage.attachments?.size === message.attachments?.size) return; // embed unfurl etc., not a real edit
      const copy = await this.#copyOf(message.id);
      if (!copy) {
        await this.#post(message); // never mirrored (e.g. posted while the bot was offline)
        return;
      }
      await copy.edit({ embeds: [mirrorEmbed(message, { edited: true })], allowedMentions: { parse: [] } });
      this.stats.edited += 1;
      this.stats.lastAt = new Date().toISOString();
    }, `edit ${newMessage && newMessage.id}`);
  }

  onDelete(message) {
    if (!message || message.channelId !== this.channelId || !this.links[message.id]) return Promise.resolve();
    return this.#queue(async () => {
      const copy = await this.#copyOf(message.id);
      if (copy) await copy.delete();
      delete this.links[message.id];
      this.stats.deleted += 1;
      this.stats.lastAt = new Date().toISOString();
      await this.#save().catch((e) => this.log.error(`[Mirror] could not save links: ${e.message}`));
    }, `delete ${message.id}`);
  }

  /** Waits for queued work (used by tests and shutdown). */
  idle() {
    return this.chain;
  }

  register(client, { Events }) {
    client.on(Events.MessageCreate, (m) => this.onCreate(m));
    client.on(Events.MessageUpdate, (a, b) => this.onUpdate(a, b));
    client.on(Events.MessageDelete, (m) => this.onDelete(m));
  }
}

module.exports = { MessageMirror };
