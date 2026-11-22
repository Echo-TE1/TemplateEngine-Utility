'use strict';

const { LOG_TYPES, labelFor } = require('./logChannels');

const DOC = 'reactionroles';

// One emoji per log, shown on the reaction-role message.
const EMOJIS = {
  platform_status: '📡',
  website_status: '🌐',
  api_status: '⚙️',
  game_updates: '🎮',
  bot_updates: '🤖',
};

/**
 * Reaction roles + private alerts. Reacting to the posted message subscribes the person to a log:
 * they get that log's role (if the owner set one) and the bot DMs them whatever is posted to that log.
 * Removing the reaction undoes both.
 */
class ReactionRoles {
  constructor({ store, client = null, log = console }) {
    this.store = store;
    this.client = client;
    this.log = log;
    this.roles = {}; // log -> role id
    this.message = null; // { channelId, messageId }
    this.subs = {}; // log -> [user ids]
  }

  async load() {
    const doc = (await this.store.getDoc(DOC)) || {};
    this.roles = doc.roles || {};
    this.message = doc.message || null;
    this.subs = doc.subs || {};
  }

  async #save() {
    await this.store.setDoc(DOC, { roles: this.roles, message: this.message, subs: this.subs });
  }

  roleFor(type) {
    return this.roles[type] || null;
  }

  async setRole(type, roleId) {
    const before = this.roles[type];
    if (roleId) this.roles[type] = roleId;
    else delete this.roles[type];
    try {
      await this.#save();
    } catch (error) {
      if (before) this.roles[type] = before;
      else delete this.roles[type];
      throw error;
    }
  }

  async setMessage(channelId, messageId) {
    this.message = { channelId, messageId };
    await this.#save();
  }

  logForEmoji(name) {
    return Object.keys(EMOJIS).find((type) => EMOJIS[type] === name) || null;
  }

  subscribers(type) {
    return [...(this.subs[type] || [])];
  }

  async subscribe(type, userId) {
    const list = (this.subs[type] = this.subs[type] || []);
    if (list.includes(userId)) return false;
    list.push(userId);
    await this.#save();
    return true;
  }

  async unsubscribe(type, userId) {
    const list = this.subs[type] || [];
    if (!list.includes(userId)) return false;
    this.subs[type] = list.filter((id) => id !== userId);
    await this.#save();
    return true;
  }

  isWatching(reaction) {
    return Boolean(this.message && reaction.message.id === this.message.messageId);
  }

  async #member(reaction, user) {
    const guild = reaction.message.guild;
    return guild ? guild.members.fetch(user.id) : null;
  }

  /** Someone added a reaction to the role message. */
  async handleAdd(reaction, user) {
    if (user.bot) return;
    if (reaction.partial) reaction = await reaction.fetch();
    if (!this.isWatching(reaction)) return;
    const type = this.logForEmoji(reaction.emoji.name);
    if (!type) return;

    const label = labelFor(type);
    await this.subscribe(type, user.id);
    const notes = [];
    const roleId = this.roleFor(type);
    if (roleId) {
      try {
        const member = await this.#member(reaction, user);
        if (member) await member.roles.add(roleId, 'Reaction role');
      } catch (error) {
        this.log.warn(`[ReactionRoles] could not give role ${roleId} to ${user.id}: ${error.message}`);
        notes.push('I could not give you the role (the bot needs Manage Roles and its role must be above it).');
      }
    }
    try {
      await user.send({
        content: `<@${user.id}>`,
        embeds: [
          {
            color: 0x57f287,
            title: `✅ Subscribed to ${label}`,
            description:
              `I will DM you when something is posted to **${label}**.\nRemove your reaction to stop.` +
              (notes.length ? `\n\n⚠️ ${notes.join('\n⚠️ ')}` : ''),
          },
        ],
        allowedMentions: { users: [user.id] },
      });
    } catch {
      this.log.warn(`[ReactionRoles] ${user.id} has DMs closed; they are subscribed but cannot receive DMs.`);
    }
  }

  /** Someone removed a reaction from the role message. */
  async handleRemove(reaction, user) {
    if (user.bot) return;
    if (reaction.partial) reaction = await reaction.fetch();
    if (!this.isWatching(reaction)) return;
    const type = this.logForEmoji(reaction.emoji.name);
    if (!type) return;
    await this.unsubscribe(type, user.id);
    const roleId = this.roleFor(type);
    if (roleId) {
      try {
        const member = await this.#member(reaction, user);
        if (member) await member.roles.remove(roleId, 'Reaction role removed');
      } catch (error) {
        this.log.warn(`[ReactionRoles] could not remove role ${roleId} from ${user.id}: ${error.message}`);
      }
    }
  }

  /**
   * DMs everyone subscribed to a log with what was just posted there (embeds only, never files).
   * Never throws; people with closed DMs are skipped.
   */
  async dm(type, payload) {
    const ids = this.subscribers(type);
    if (!ids.length || !payload || payload.files) return 0;
    const label = labelFor(type);
    let sent = 0;
    for (const id of ids) {
      try {
        const user = await this.client.users.fetch(id);
        await user.send({
          content: `<@${id}> **${label}**`,
          embeds: payload.embeds || [],
          allowedMentions: { users: [id] },
        });
        sent += 1;
      } catch (error) {
        this.log.warn(`[ReactionRoles] could not DM ${id}: ${error.message}`);
      }
    }
    return sent;
  }
}

module.exports = { ReactionRoles, EMOJIS, LOG_TYPES };
