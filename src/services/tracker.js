'use strict';

const { createHash } = require('node:crypto');
const { diff } = require('../lib/diff');
const { annotate } = require('../lib/match');
const embeds = require('../embeds');

const UNKNOWN_MESSAGE = 10008; // Discord API error: the message was deleted
const MAX_SEND_ATTEMPTS = 3;

class Tracker {
  /**
   * @param {object} deps
   * @param {import('discord.js').Client} deps.client
   * @param {object} deps.store         createStore() result
   * @param {object} deps.config        src/config.js
   * @param {object} deps.logChannels   LogChannels: the channel comes from /setup log (Platform Status)
   * @param {Function} [deps.fetchStatus]  (url) => normalized snapshot (replaceable for tests)
   */
  constructor({ client, store, config, logChannels, fetchStatus = require('./statusClient').fetchStatus, log = console }) {
    this.client = client;
    this.store = store;
    this.config = config;
    this.logChannels = logChannels;
    this.fetchStatus = fetchStatus;
    this.log = log;

    this.state = null;
    this.lastSnapshot = null;
    this.polling = false;
    this.running = false;
    this.timer = null;
    this.failures = 0;
    this.retry = []; // alerts that could not be sent yet
  }

  /** Where the live message and alerts go: the Platform Status channel set with /setup log (or null). */
  get channelId() {
    return this.logChannels.get('platform_status');
  }

  get ctx() {
    return { statusPageUrl: this.config.statusPageUrl, pollSeconds: this.config.pollSeconds };
  }

  // ---- lifecycle -----------------------------------------------------------------------
  async start() {
    this.state = await this.store.loadState();
    this.running = true;
    this.log.log(
      `[Tracker] Started. Checking every ${this.config.pollSeconds}s, alert scope: ${this.config.alertScope}.`,
    );
    this.#schedule(0);
  }

  stop() {
    this.running = false;
    clearTimeout(this.timer);
  }

  #schedule(ms) {
    this.timer = setTimeout(async () => {
      await this.poll();
      if (this.running) this.#schedule(this.config.pollSeconds * 1000);
    }, ms);
  }

  // ---- data ----------------------------------------------------------------------------
  async fetchSnapshot() {
    const raw = await this.fetchStatus(this.config.statusUrl);
    const snapshot = annotate(raw, this.config.trackedServices);
    this.lastSnapshot = snapshot;
    return snapshot;
  }

  /** For /robloxstatus: a recent snapshot, falling back to the last one if Roblox cannot be reached. */
  async getSnapshot(maxAgeMs = 15_000) {
    const last = this.lastSnapshot;
    if (last && Date.now() - Date.parse(last.fetchedAt) < maxAgeMs) return { snapshot: last, stale: false };
    try {
      return { snapshot: await this.fetchSnapshot(), stale: false };
    } catch (error) {
      if (last) return { snapshot: last, stale: true, error };
      throw error;
    }
  }

  getHistory(limit) {
    return this.store.getHistory(limit);
  }

  // ---- one check -----------------------------------------------------------------------
  async poll() {
    if (this.polling) return; // never run two checks at once
    this.polling = true;
    try {
      let snapshot;
      try {
        snapshot = await this.fetchSnapshot();
        this.failures = 0;
        if (!this.loggedFirstCheck) {
          this.loggedFirstCheck = true;
          this.log.log(`[Tracker] First check OK: ${snapshot.services.length} services, ${snapshot.incidents.length} incident(s).`);
        }
      } catch (error) {
        this.failures += 1;
        this.log.warn(`[Tracker] Could not read Roblox status (${this.failures} in a row): ${error.message}`);
        if (this.failures === 3 && this.lastSnapshot) {
          // Tell readers of the live message that the data is getting old.
          await this.#updateBoard(
            this.lastSnapshot,
            `Could not reach Roblox's status API. Showing data from ${this.#ago(this.lastSnapshot.fetchedAt)}.`,
          );
        }
        return;
      }

      const { next, events, history } = diff(this.state, snapshot, {
        scope: this.config.alertScope,
        baseline: !this.state.initialized && !this.config.notifyOnStartup,
        resolveConfirm: this.config.resolveConfirmPolls,
      });

      // Save first, then announce: a crash in between can only lose an alert, never repeat one.
      this.state = next;
      for (const record of history) await this.store.addHistory(record);
      await this.store.saveState(this.state);

      await this.#deliver(events);
      await this.#updateBoard(snapshot);
    } catch (error) {
      this.log.error(`[Tracker] Poll failed: ${error.stack || error.message}`);
    } finally {
      this.polling = false;
    }
  }

  #ago(iso) {
    return `<t:${Math.floor(Date.parse(iso) / 1000)}:R>`;
  }

  // ---- Discord output --------------------------------------------------------------------
  async #channel(id) {
    const channel = this.client.channels.cache.get(id) || (await this.client.channels.fetch(id));
    if (!channel || !channel.isTextBased()) throw new Error(`Channel ${id} is missing or is not a text channel.`);
    return channel;
  }

  async #deliver(events) {
    if (!this.channelId) return; // /setup log not done yet: tracking only
    const queue = [...this.retry, ...events.map((event) => ({ event, attempts: 0 }))];
    this.retry = [];

    for (const item of queue) {
      try {
        await this.#send(item.event);
      } catch (error) {
        item.attempts += 1;
        this.log.warn(`[Tracker] Could not send ${item.event.type} (try ${item.attempts}): ${error.message}`);
        if (item.attempts < MAX_SEND_ATTEMPTS) this.retry.push(item);
      }
    }
  }

  async #send(event) {
    const channel = await this.#channel(this.channelId);
    const roleId = this.config.alertRoleId;
    const send = (embed, { ping = false, replyTo = null } = {}) =>
      channel.send({
        content: ping && roleId ? `<@&${roleId}>` : undefined,
        embeds: [embed],
        allowedMentions: ping && roleId ? { roles: [roleId] } : { parse: [] },
        reply: replyTo ? { messageReference: replyTo, failIfNotExists: false } : undefined,
      });

    switch (event.type) {
      case 'incident_open': {
        const message = await send(embeds.incidentOpenEmbed(event.incident, this.ctx), { ping: true });
        // Remember the message so updates and the resolution can reply to it.
        const record = this.state.incidents[event.incident.id];
        if (record) {
          record.messageId = message.id;
          await this.store.saveState(this.state);
        }
        break;
      }
      case 'incident_update': {
        const record = this.state.incidents[event.incident.id];
        await send(embeds.incidentUpdateEmbed(event.incident, event.updates, this.ctx), {
          replyTo: record && record.messageId,
        });
        break;
      }
      case 'incident_resolved':
        await send(embeds.incidentResolvedEmbed(event.record, event.resolvedAt, this.ctx), {
          ping: true,
          replyTo: event.record.messageId,
        });
        break;
      case 'service_change':
        await send(embeds.serviceChangeEmbed(event.changes, this.ctx));
        break;
      default:
        break;
    }
  }

  /** Edits the one live status message. Only touches Discord when something changed (or it is getting old). */
  async #updateBoard(snapshot, warning = null) {
    const channelId = this.channelId;
    if (!channelId) return;

    const embed = embeds.boardEmbed(snapshot, this.ctx, warning);
    const { timestamp, ...content } = embed.toJSON(); // the timestamp changes every poll, so it is not compared
    const hash = createHash('sha1').update(JSON.stringify(content)).digest('hex');

    const saved = this.state.statusMessage;
    const sameChannel = saved && saved.channelId === channelId;
    const age = saved ? Date.now() - Date.parse(saved.editedAt) : Infinity;
    if (sameChannel && saved.hash === hash && age < this.config.boardRefreshSeconds * 1000) return;

    const channel = await this.#channel(channelId);
    let message = null;
    if (sameChannel) {
      try {
        message = await channel.messages.fetch(saved.messageId);
        await message.edit({ embeds: [embed], allowedMentions: { parse: [] } });
      } catch (error) {
        if (error.code !== UNKNOWN_MESSAGE) throw error;
        message = null; // someone deleted it: post a fresh one
      }
    }
    if (!message) message = await channel.send({ embeds: [embed], allowedMentions: { parse: [] } });

    this.state.statusMessage = {
      channelId,
      messageId: message.id,
      hash,
      editedAt: new Date().toISOString(),
    };
    await this.store.saveState(this.state);
  }
}

module.exports = { Tracker };
