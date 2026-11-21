'use strict';

const { SlashCommandBuilder } = require('discord.js');
const { boardEmbed, historyEmbed, errorEmbed } = require('../embeds');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('robloxstatus')
    .setDescription('Show the current Roblox status, or the history of recent incidents.')
    .addStringOption((option) =>
      option
        .setName('view')
        .setDescription('What to show (default: current status).')
        .addChoices(
          { name: 'Current status', value: 'current' },
          { name: 'Incident history', value: 'history' },
        ),
    )
    .addIntegerOption((option) =>
      option
        .setName('count')
        .setDescription('History only: how many incidents to show (1-10, default 5).')
        .setMinValue(1)
        .setMaxValue(10),
    ),

  async execute(interaction, { tracker }) {
    await interaction.deferReply();
    const view = interaction.options.getString('view') || 'current';

    if (view === 'history') {
      const records = await tracker.getHistory(interaction.options.getInteger('count') || 5);
      await interaction.editReply({ embeds: [historyEmbed(records, tracker.ctx)] });
      return;
    }

    try {
      const { snapshot, stale } = await tracker.getSnapshot();
      const warning = stale
        ? `Could not reach Roblox just now. Showing data from <t:${Math.floor(Date.parse(snapshot.fetchedAt) / 1000)}:R>.`
        : null;
      await interaction.editReply({ embeds: [boardEmbed(snapshot, tracker.ctx, warning)] });
    } catch (error) {
      await interaction.editReply({
        embeds: [errorEmbed('Roblox Status Unavailable', `I could not read Roblox's status page.\n\`${error.message}\``)],
      });
    }
  },
};
