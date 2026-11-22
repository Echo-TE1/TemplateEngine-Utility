'use strict';

const { createHash } = require('node:crypto');
const { diff } = require('../lib/diff');
const { annotate } = require('../lib/match');
const embeds = require('../embeds');
const { norm } = require('../lib/match');

const UNKNOWN_MESSAGE = 10008; // Discord API error: the message was deleted
const MAX_SEND_ATTEMPTS = 3;

class Tracker {
  /**
   * @param {object} deps
   * @param {import('discord.js').Client} deps.client
   * @param {object} deps.store         createStore() result
   * @param {object} deps.config        src/config.js
   * @param {object} deps.logChannels   LogChannels: the channel comes from /setup log (Platform Status)
   * @param {object} [deps.serviceRoles]  ServiceRoles: which roles to ping per service (/setup role)
   * @param {object} [deps.notifier]      Notifier: posts to the other logs (Website/API Status, Bot Updates)
   * @param {Function} [deps.fetchStatus]  (url) => normalized snapshot (replaceable for tests)
   */
  constructor({ client, store, config, logChannels, serviceRoles, notifier, fetchStatus = require('./statusClient').fetchStatus, log = console }) {
    this.client = client;
    this.store = store;
    this.config = config;
    this.logChannels = logChannels;
    this.serviceRoles = serviceRoles || { rolesFor: () => (config.alertRoleId ? [config.alertRoleId] : []) };
    this.notifier = notifier || { post: async () => false };
    this.fetchStatus = fetchStatus;
    this.log = log;
    this.lastSuccessAt = null;
    this.failureSince = null;
    this.apiAlerted = false;

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

  /** Incidents that are open right now (for uptime numbers). */
  openIncidents() {
    return Object.values((this.state && this.state.incidents) || {});
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
        this.lastSuccessAt = Date.now();
        if (!this.loggedFirstCheck) {
          this.loggedFirstCheck = true;
          this.log.log(`[Tracker] First check OK: ${snapshot.services.length} services, ${snapshot.incidents.length} incident(s).`);
        }
        if (this.apiAlerted) {
          this.apiAlerted = false;
          await this.notifier.post('bot_updates', {
            embeds: [embeds.botNoticeEmbed('✅ Roblox status API reachable again', 'The bot can read Roblox\'s status data again.', 'ok')],
          });
        }
        this.failureSince = null;
      } catch (error) {
        this.failures += 1;
        this.log.warn(`[Tracker] Could not read Roblox status (${this.failures} in a row): ${error.message}`);
        await this.#maybeAlertUnreachable(error);
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
      await this.#crossPost(events);
      await this.#updateBoard(snapshot);
    } catch (error) {
      this.log.error(`[Tracker] Poll failed: ${error.stack || error.message}`);
    } finally {
      this.polling = false;
    }
  }

  /** If Roblox's status API stays unreachable for a while, tell the Bot Updates log (once). */
  async #maybeAlertUnreachable(error) {
    if (!this.failureSince) this.failureSince = Date.now();
    const minutes = (Date.now() - this.failureSince) / 60_000;
    const after = this.config.botAlertAfterMinutes ?? 10;
    if (this.apiAlerted || minutes < after) return;
    this.apiAlerted = true;
    await this.notifier.post('bot_updates', {
      embeds: [
        embeds.botNoticeEmbed(
          '⚠️ Roblox status API unreachable',
          `The bot has not been able to read Roblox's status data for about ${Math.round(minutes)} minutes, so the live status may be out of date.\n\`${error.message}\``,
          'bad',
        ),
      ],
    });
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
    // roles: the Discord roles to ping (set per service with /setup role).
    const send = (embed, { roles = [], replyTo = null } = {}) =>
      channel.send({
        content: roles.length ? roles.map((id) => `<@&${id}>`).join(' ') : undefined,
        embeds: [embed],
        allowedMentions: roles.length ? { roles } : { parse: [] },
        reply: replyTo ? { messageReference: replyTo, failIfNotExists: false } : undefined,
      });

    switch (event.type) {
      case 'incident_open': {
        const message = await send(embeds.incidentOpenEmbed(event.incident, this.ctx), {
          roles: this.serviceRoles.rolesFor(event.incident.trackedTerms || []),
        });
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
          roles: this.serviceRoles.rolesFor(event.record.trackedTerms || []),
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

  /**
   * Copies Roblox events to the Website Status and API Status logs (no pings there; the Platform
   * Status log already pings). Website things go to Website Status, everything else to API Status.
   */
  async #crossPost(events) {
    const isWebsite = (name) => norm(name) === 'website';
    const post = (type, embed) => this.notifier.post(type, { embeds: [embed] });

    for (const event of events) {
      let services = [];
      let embed = null;
      if (event.type === 'incident_open') {
        services = event.incident.services;
        embed = embeds.incidentOpenEmbed(event.incident, this.ctx);
      } else if (event.type === 'incident_update') {
        services = event.incident.services;
        embed = embeds.incidentUpdateEmbed(event.incident, event.updates, this.ctx);
      } else if (event.type === 'incident_resolved') {
        services = event.record.services;
        embed = embeds.incidentResolvedEmbed(event.record, event.resolvedAt, this.ctx);
      } else if (event.type === 'service_change') {
        const web = event.changes.filter((c) => isWebsite(c.name));
        const api = event.changes.filter((c) => !isWebsite(c.name));
        if (web.length) await post('website_status', embeds.serviceChangeEmbed(web, this.ctx));
        if (api.length) await post('api_status', embeds.serviceChangeEmbed(api, this.ctx));
        continue;
      }
      if (!embed) continue;
      if (services.some(isWebsite)) await post('website_status', embed);
      if (services.some((s) => !isWebsite(s))) await post('api_status', embed);
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
