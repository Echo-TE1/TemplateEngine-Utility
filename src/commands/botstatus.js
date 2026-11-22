'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');
const { errorEmbed, botStatusEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');
const { LOG_TYPES } = require('../services/logChannels');
const { duration, ts } = require('../lib/format');

function collect(context, client) {
  const { economy, tracker, health, news, mirror, memberCount, mirrorIntentRefused, store, logChannels, config, startedAt } = context;
  let problems = false;

  // Roblox status API (the main data source)
  let statusApi;
  if (tracker.lastSuccessAt) {
    const iso = new Date(tracker.lastSuccessAt).toISOString();
    statusApi = `${tracker.failures ? '✕' : '✓'} Last good check ${ts(iso, 'R')} • every ${config.pollSeconds}s`;
    if (tracker.failures) {
      problems = true;
      statusApi += `\n${tracker.failures} failed check(s) in a row`;
    }
  } else {
    statusApi = '… No successful check yet';
    problems = problems || tracker.failures > 0;
  }

  // Direct health checks
  const healthRows = health
    ? health.targets.map((target) => {
        const r = health.latest[target.name];
        if (!r) return { name: target.name, ok: null, text: 'waiting for first check' };
        if (!r.ok) problems = true;
        return {
          name: target.name,
          ok: r.ok,
          text: r.ok ? `HTTP ${r.httpStatus} in ${r.ms} ms` : r.error || `HTTP ${r.httpStatus}`,
        };
      })
    : [];

  // News feeds
  const newsRows = news
    ? news.feeds.map((feed) => {
        const s = news.status[feed.name];
        if (!s) return { name: feed.name, ok: null, text: 'waiting for first check' };
        if (!s.ok) problems = true;
        return { name: feed.name, ok: s.ok, text: s.ok ? `OK ${ts(s.at, 'R')}${s.note ? ` (${s.note})` : ''}` : s.error };
      })
    : [];

  // Message mirror
  let mirrorText = null;
  if (mirror) {
    const st = mirror.stats;
    mirrorText = `✓ ${st.mirrored} copied • ${st.edited} edited • ${st.deleted} deleted`;
    if (st.lastError) {
      problems = true;
      mirrorText = `✕ ${st.errors} error(s). Last: ${st.lastError}`;
    }
  } else if (mirrorIntentRefused) {
    problems = true;
    mirrorText = '✕ OFF: Message Content Intent is not enabled in the Developer Portal';
  } else if (!config.mirrorEnabled) {
    mirrorText = 'Disabled';
  }

  // Member count note
  let memberText = null;
  if (memberCount) {
    const st = memberCount.stats;
    if (st.error) {
      problems = true;
      memberText = `✕ ${st.error}`;
    } else if (st.count != null) {
      memberText = `✓ ${st.count.toLocaleString('en-US')} members`;
    } else {
      memberText = '… Not checked yet';
    }
  }

  let economyText = null;
  if (economy) {
    if (economy.status.ok === false) {
      problems = true;
      economyText = `✕ ${economy.status.error}`;
    } else if (economy.updatedAt) {
      economyText = `✓ ${economy.items.size.toLocaleString('en-US')} items, updated ${ts(economy.updatedAt, 'R')}`;
    } else {
      economyText = '… Not checked yet';
    }
  }

  // Database
  let database = store.persistent
    ? `✓ ${store.kind} (saved permanently)`
    : `! ${store.kind} (lost when the host redeploys)`;
  if (store.fellBack) {
    database = '✕ MongoDB could not be reached, so local files are used (lost on redeploy)';
    problems = true;
  } else if (!store.persistent) {
    problems = true;
  }

  const setCount = LOG_TYPES.filter((t) => logChannels.get(t.value)).length;
  const mb = Math.round(process.memoryUsage().rss / 1_048_576);
  const ping = client.ws && client.ws.ping >= 0 ? `${Math.round(client.ws.ping)} ms` : 'n/a';

  return {
    problems,
    uptime: duration(Date.now() - (startedAt || Date.now() - process.uptime() * 1000)),
    ping,
    memory: `${mb} MB`,
    database,
    statusApi,
    health: healthRows,
    news: newsRows,
    mirror: mirrorText,
    memberCount: memberText,
    economy: economyText,
    logs: `${setCount}/${LOG_TYPES.length} set up` + (setCount < LOG_TYPES.length ? ' (see /setup view)' : ''),
    version: (process.env.RENDER_GIT_COMMIT || '').slice(0, 7),
  };
}

module.exports = {
  data: new SlashCommandBuilder()
    .setName('botstatus')
    .setDescription('Bot owner only: show whether the bot and its data sources are healthy.'),

  async execute(interaction, context) {
    if (interaction.user.id !== context.config.ownerId) {
      await interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
      return;
    }

    const build = async () => {
      try {
        return { embeds: [botStatusEmbed(collect(context, interaction.client))] };
      } catch (error) {
        console.error(`[Cmd] /botstatus failed: ${error.stack || error.message}`);
        return { embeds: [errorEmbed('Bot Status Failed', `\`${error.message}\``)] };
      }
    };

    await respondWhenReady(interaction, build, { ephemeral: true });
  },
};
