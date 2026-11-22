'use strict';

const DOC = 'profilepanel';

/**
 * The permanent "Search Roblox User" message. Its buttons use fixed ids and are answered by the bot's
 * interaction handler, so they keep working after restarts. On every start the message is checked and
 * re-posted if someone deleted it.
 */
class ProfilePanel {
  constructor({ store, client, buildPayload, log = console }) {
    Object.assign(this, { store, client, buildPayload, log });
    this.panel = null; // { channelId, messageId }
  }

  async load() {
    this.panel = ((await this.store.getDoc(DOC)) || {}).panel || null;
  }

  async #channel(id) {
    try {
      const channel = this.client.channels.cache.get(id) || (await this.client.channels.fetch(id));
      return channel && channel.isTextBased() ? channel : null;
    } catch {
      return null;
    }
  }

  /** Posts the panel in a channel (replacing the previous one, if any). */
  async publish(channel) {
    const previous = this.panel;
    const message = await channel.send(this.buildPayload());
    this.panel = { channelId: channel.id, messageId: message.id };
    await this.store.setDoc(DOC, { panel: this.panel });
    if (previous && previous.messageId !== message.id) {
      const old = await this.#channel(previous.channelId);
      if (old) await old.messages.delete(previous.messageId).catch(() => {});
    }
    return message;
  }

  /** @returns {Promise<'none'|'missing-channel'|'reposted'|'ok'>} */
  async ensure() {
    if (!this.panel) return 'none';
    const channel = await this.#channel(this.panel.channelId);
    if (!channel) {
      this.log.warn(`[Profile] Panel channel ${this.panel.channelId} is not reachable; run /profile tracker panel again.`);
      return 'missing-channel';
    }
    let message = null;
    try {
      message = await channel.messages.fetch(this.panel.messageId);
    } catch {
      message = null;
    }
    if (!message) {
      await this.publish(channel);
      this.log.log('[Profile] Panel message was gone, posted it again.');
      return 'reposted';
    }
    await message.edit(this.buildPayload()).catch((e) => this.log.warn(`[Profile] could not refresh panel: ${e.message}`));
    return 'ok';
  }
}

module.exports = { ProfilePanel };
