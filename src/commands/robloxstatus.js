'use strict';

const { SlashCommandBuilder } = require('discord.js');
const { boardEmbed, historyEmbed, uptimeEmbed, errorEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');
const { uptimeStats } = require('../lib/stats');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('robloxstatus')
    .setDescription('Show the current Roblox status, recent incidents, or uptime.')
    .addStringOption((option) =>
      option
        .setName('view')
        .setDescription('What to show (default: current status).')
        .addChoices(
          { name: 'Current status', value: 'current' },
          { name: 'Incident history', value: 'history' },
          { name: 'Uptime (incident based)', value: 'uptime' },
        ),
    )
    .addIntegerOption((option) =>
      option
        .setName('count')
        .setDescription('History only: how many incidents to show (1-10, default 5).')
        .setMinValue(1)
        .setMaxValue(10),
    )
    .addIntegerOption((option) =>
      option
        .setName('days')
        .setDescription('Uptime only: how many days to look back (1-90, default 30).')
        .setMinValue(1)
        .setMaxValue(90),
    ),

  async execute(interaction, { tracker }) {
    const view = interaction.options.getString('view') || 'current';
    const count = interaction.options.getInteger('count') || 5;
    const days = interaction.options.getInteger('days') || 30;

    // Builds the message. It never throws: problems become an error embed.
    const build = async () => {
      try {
        if (view === 'history') {
          return { embeds: [historyEmbed(await tracker.getHistory(count), tracker.ctx)] };
        }
        if (view === 'uptime') {
          // Tracked services that exist on Roblox's page are listed even with no incidents (100%).
          const virtual = (tracker.lastSnapshot && tracker.lastSnapshot.tracking.virtual) || [];
          const include = tracker.config.trackedServices.filter((s) => !virtual.includes(s));
          const history = await tracker.getHistory(5000);
          const stats = uptimeStats(history, { days, open: tracker.openIncidents(), include });
          return { embeds: [uptimeEmbed(stats, tracker.ctx)] };
        }
        const { snapshot, stale } = await tracker.getSnapshot();
        const warning = stale
          ? `Could not reach Roblox just now. Showing data from <t:${Math.floor(Date.parse(snapshot.fetchedAt) / 1000)}:R>.`
          : null;
        return { embeds: [boardEmbed(snapshot, tracker.ctx, warning)] };
      } catch (error) {
        return {
          embeds: [errorEmbed('Roblox Status Unavailable', `I could not read Roblox's status page.\n\`${error.message}\``)],
        };
      }
    };

    await respondWhenReady(interaction, build);
  },
};
