'use strict';

const { AttachmentBuilder } = require('discord.js');
const { startLoop } = require('../lib/loop');
const { botNoticeEmbed } = require('../embeds');

const MAX_BYTES = 7_500_000; // Discord's upload limit is higher, but stay well under it
const JST_OFFSET_MS = 9 * 3_600_000;

const jstDate = (ms = Date.now()) => new Date(ms + JST_OFFSET_MS).toISOString().slice(0, 10);

/** Everything the bot remembers, as JSON. It contains no tokens or secrets. */
async function buildBackup({ store, logChannels, serviceRoles, now = new Date() }) {
  const history = await store.getHistory(100_000);
  const data = {
    exportedAt: now.toISOString(),
    storage: store.kind,
    logChannels: logChannels.map,
    serviceRoles: serviceRoles.map,
    health: await store.getDoc('health'),
    news: await store.getDoc('news'),
    incidentHistory: history,
  };
  const buffer = Buffer.from(JSON.stringify(data, null, 2), 'utf8');
  return {
    name: `bot-backup-${now.toISOString().slice(0, 10)}.json`,
    buffer,
    counts: { incidents: history.length, logs: Object.keys(logChannels.map).length, roles: serviceRoles.list().length },
  };
}

const toAttachment = (backup) => new AttachmentBuilder(backup.buffer, { name: backup.name });

/** Once a day (after midnight JST) posts a backup file to the Bot Updates log. */
class BackupScheduler {
  constructor({ store, logChannels, serviceRoles, notifier, config, log = console }) {
    Object.assign(this, { store, logChannels, serviceRoles, notifier, config, log });
    this.loop = null;
  }

  start() {
    if (!this.config.backupDaily) return;
    this.loop = startLoop(() => this.check(), 15 * 60_000, { name: 'Backup', delayMs: 60_000, log: this.log });
  }

  stop() {
    if (this.loop) this.loop.stop();
  }

  async check(nowMs = Date.now()) {
    const today = jstDate(nowMs);
    const saved = await this.store.getDoc('backup');
    if (!saved.lastDate) {
      await this.store.setDoc('backup', { lastDate: today }); // first start: the first backup is tomorrow
      return false;
    }
    if (saved.lastDate === today || !this.logChannels.get('bot_updates')) return false;

    const backup = await buildBackup({ store: this.store, logChannels: this.logChannels, serviceRoles: this.serviceRoles, now: new Date(nowMs) });
    const summary = `${backup.counts.incidents} incident(s), ${backup.counts.logs} log channel(s), ${backup.counts.roles} role(s) • ${Math.round(backup.buffer.length / 1024)} KB`;

    let payload;
    if (backup.buffer.length > MAX_BYTES) {
      payload = { embeds: [botNoticeEmbed('⚠️ Daily backup skipped', `The backup is too large to upload (${summary}). Run \`/backup\` for a copy.`, 'warn')] };
    } else {
      payload = { embeds: [botNoticeEmbed('💾 Daily backup', summary)], files: [toAttachment(backup)] };
    }
    const posted = await this.notifier.post('bot_updates', payload);
    if (posted) await this.store.setDoc('backup', { lastDate: today });
    return posted;
  }
}

module.exports = { buildBackup, toAttachment, BackupScheduler, jstDate };
