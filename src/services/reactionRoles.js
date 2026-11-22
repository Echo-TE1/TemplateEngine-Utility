'use strict';

const { LOG_TYPES, LOG_DESCRIPTIONS, labelFor } = require('./logChannels');

const DOC = 'reactionroles';

// Names of the custom emoji to look for in the server. If one is missing, the standard emoji below is used.
const CUSTOM_NAMES = {
  platform_status: 'platform_status',
  website_status: 'website_status',
  api_status: 'api_status',
  game_updates: 'roblox_update',
  bot_updates: 'bot_update',
};

// One reaction per log. Reactions can only be emoji, so they are written as escapes.
const EMOJIS = {
  platform_status: '\u{1F4E1}',
  website_status: '\u{1F310}',
  api_status: '\u2699\uFE0F',
  game_updates: '\u{1F3AE}',
  bot_updates: '\u{1F916}',
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
    this.custom = {}; // log -> { id, name, animated } found in the server
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

  /** The server the roles live in: the reaction message's, else any log channel's, else the mirror channel's. */
  async #guild(logChannels, config) {
    const ids = [];
    if (this.message) ids.push(this.message.channelId);
    for (const t of LOG_TYPES) ids.push(logChannels.get(t.value));
    if (config && config.mirrorChannelId) ids.push(config.mirrorChannelId);
    for (const id of ids.filter(Boolean)) {
      try {
        const channel = this.client.channels.cache.get(id) || (await this.client.channels.fetch(id));
        if (channel && channel.guild) return channel.guild;
      } catch {
        // try the next one
      }
    }
    return null;
  }

  /**
   * Makes sure every log has a role. A role that was deleted is recreated; an existing role with the
   * right name is reused instead of making a duplicate. New logs get their role automatically.
   * @returns {Promise<string[]>} names of the roles that were created
   */
  async ensureRoles(logChannels, config) {
    const guild = await this.#guild(logChannels, config);
    if (!guild) {
      this.log.warn('[ReactionRoles] No server found yet, so no roles were created. Set a log channel with /setup log first.');
      return [];
    }
    const existing = await guild.roles.fetch();
    const created = [];
    for (const type of LOG_TYPES) {
      const current = this.roles[type.value];
      if (current && existing.has(current)) continue;
      const name = `${type.name} Alerts`;
      let role = [...existing.values()].find((r) => r.name === name);
      if (!role) {
        try {
          role = await guild.roles.create({ name, mentionable: false, reason: 'Reaction role for the bot logs' });
          created.push(name);
        } catch (error) {
          this.log.warn(`[ReactionRoles] could not create "${name}": ${error.message} (the bot needs Manage Roles)`);
          continue;
        }
      }
      await this.setRole(type.value, role.id);
    }
    if (created.length) this.log.log(`[ReactionRoles] Created roles: ${created.join(', ')}`);
    return created;
  }

  /** Looks for the custom emoji (by name) in the server; logs without one use the standard emoji. */
  async refreshEmojis(guild) {
    this.custom = {};
    if (!guild || !guild.emojis) return;
    try {
      const all = await guild.emojis.fetch();
      for (const type of Object.keys(CUSTOM_NAMES)) {
        const found = [...all.values()].find((e) => e.name === CUSTOM_NAMES[type]);
        if (found) this.custom[type] = { id: found.id, name: found.name, animated: Boolean(found.animated) };
      }
    } catch (error) {
      this.log.warn(`[ReactionRoles] could not read server emoji: ${error.message}`);
    }
  }

  /** What to pass to message.react, and the key it has in message.reactions.cache. */
  reactArg(type) {
    const c = this.custom[type];
    return c ? `${c.name}:${c.id}` : EMOJIS[type];
  }

  reactKey(type) {
    const c = this.custom[type];
    return c ? c.id : EMOJIS[type];
  }

  /** How the emoji is written inside message text. */
  display(type) {
    const c = this.custom[type];
    return c ? `<${c.animated ? 'a' : ''}:${c.name}:${c.id}>` : EMOJIS[type];
  }

  /** The text of the reaction message (shows each log and its role). */
  messagePayload() {
    const lines = LOG_TYPES.map((t) => {
      const role = this.roleFor(t.value);
      return `${this.display(t.value)} **${t.name}** — ${LOG_DESCRIPTIONS[t.value]}${role ? ` (<@&${role}>)` : ''}`;
    });
    return {
      embeds: [
        {
          color: 0x5865f2,
          title: 'Get notified',
          description:
            'React to choose what you want to hear about. You get the role and the bot **DMs you** when it happens (make sure your DMs are open). Remove the reaction to stop.\n\n' +
            lines.join('\n'),
        },
      ],
      allowedMentions: { parse: [] },
    };
  }

  /** Posts a fresh reaction message in a channel and remembers it. */
  async publish(channel) {
    await this.refreshEmojis(channel.guild);
    const message = await channel.send(this.messagePayload());
    for (const t of LOG_TYPES) await message.react(this.reactArg(t.value));
    await this.setMessage(channel.id, message.id);
    return message;
  }

  /**
   * Run on every start (and by /reactionrole set): finds the saved reaction message, refreshes its text,
   * re-adds missing bot reactions, and catches up on reactions added or removed while the bot was offline.
   * If the message was deleted it is posted again in the saved channel.
   * @returns {Promise<'none'|'missing-channel'|'reposted'|'ok'>}
   */
  async ensureMessage() {
    if (!this.message) return 'none';
    let channel = null;
    try {
      channel = this.client.channels.cache.get(this.message.channelId) || (await this.client.channels.fetch(this.message.channelId));
    } catch {
      channel = null;
    }
    if (!channel || !channel.isTextBased()) {
      this.log.warn(`[ReactionRoles] Channel ${this.message.channelId} is not reachable; run /reactionrole set again.`);
      return 'missing-channel';
    }
    let message = null;
    try {
      message = await channel.messages.fetch(this.message.messageId);
    } catch {
      message = null;
    }
    if (!message) {
      await this.publish(channel);
      this.log.log('[ReactionRoles] Reaction message was gone, posted it again.');
      return 'reposted';
    }
    await this.refreshEmojis(channel.guild);
    await message.edit(this.messagePayload()).catch((e) => this.log.warn(`[ReactionRoles] could not refresh text: ${e.message}`));
    for (const t of LOG_TYPES) {
      if (!message.reactions.cache.has(this.reactKey(t.value))) await message.react(this.reactArg(t.value)).catch(() => {});
    }
    // Remove the bot's own reactions that no longer belong (e.g. the standard emoji once a custom one exists).
    const keep = new Set(LOG_TYPES.map((t) => this.reactKey(t.value)));
    for (const [key, reaction] of message.reactions.cache) {
      if (!keep.has(key) && reaction.me && this.client.user) await reaction.users.remove(this.client.user.id).catch(() => {});
    }
    await this.#catchUp(message).catch((e) => this.log.warn(`[ReactionRoles] catch-up failed: ${e.message}`));
    return 'ok';
  }

  // Make subscriptions match the reactions that are really on the message.
  async #catchUp(message) {
    for (const t of LOG_TYPES) {
      const reaction = message.reactions.cache.get(this.reactKey(t.value));
      const present = new Set();
      if (reaction) {
        const users = await reaction.users.fetch();
        for (const u of users.values()) if (!u.bot) present.add(u.id);
      }
      const roleId = this.roleFor(t.value);
      for (const id of present) {
        if (await this.subscribe(t.value, id)) {
          if (roleId) await message.guild.members.fetch(id).then((m) => m.roles.add(roleId)).catch(() => {});
        }
      }
      for (const id of this.subscribers(t.value)) {
        if (present.has(id)) continue;
        await this.unsubscribe(t.value, id);
        if (roleId) await message.guild.members.fetch(id).then((m) => m.roles.remove(roleId)).catch(() => {});
      }
    }
  }

  /** Which log a reaction's emoji stands for (custom by name or id, or the standard emoji). */
  logForEmoji(emoji) {
    if (emoji.id) {
      return Object.keys(CUSTOM_NAMES).find((t) => (this.custom[t] && this.custom[t].id === emoji.id) || CUSTOM_NAMES[t] === emoji.name) || null;
    }
    return Object.keys(EMOJIS).find((t) => EMOJIS[t] === emoji.name) || null;
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
    const type = this.logForEmoji(reaction.emoji);
    if (!type) return;

    // No DM here: people are only messaged when something is posted to the log they chose.
    await this.subscribe(type, user.id);
    const roleId = this.roleFor(type);
    if (roleId) {
      try {
        const member = await this.#member(reaction, user);
        if (member) await member.roles.add(roleId, 'Reaction role');
      } catch (error) {
        this.log.warn(`[ReactionRoles] could not give role ${roleId} to ${user.id}: ${error.message} (the bot needs Manage Roles and its role must be above it)`);
      }
    }
  }

  /** Someone removed a reaction from the role message. */
  async handleRemove(reaction, user) {
    if (user.bot) return;
    if (reaction.partial) reaction = await reaction.fetch();
    if (!this.isWatching(reaction)) return;
    const type = this.logForEmoji(reaction.emoji);
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

module.exports = { ReactionRoles, EMOJIS, CUSTOM_NAMES, LOG_TYPES };
