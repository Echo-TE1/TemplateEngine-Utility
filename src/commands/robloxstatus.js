'use strict';

const { SlashCommandBuilder } = require('discord.js');
const { boardEmbed, historyEmbed, errorEmbed } = require('../embeds');

const QUICK_MS = 2000; // answer directly if ready within this time, otherwise show "thinking..."

const trace = (interaction, step) =>
  console.log(`[Cmd] /${interaction.commandName} ${step} (+${Date.now() - interaction.createdTimestamp}ms)`);

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
    const view = interaction.options.getString('view') || 'current';
    const count = interaction.options.getInteger('count') || 5;

    // Builds the message. It never throws: problems become an error embed.
    const build = async () => {
      try {
        if (view === 'history') {
          return { embeds: [historyEmbed(await tracker.getHistory(count), tracker.ctx)] };
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

    // Usually the answer is ready in well under a second, so reply directly.
    const work = build();
    let timer;
    const early = await Promise.race([
      work,
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(null), QUICK_MS);
      }),
    ]);
    clearTimeout(timer);

    if (early) {
      trace(interaction, 'replying');
      await interaction.reply(early);
      trace(interaction, 'replied');
      return;
    }

    trace(interaction, 'deferring');
    await interaction.deferReply();
    trace(interaction, 'deferred');
    const payload = await work;
    trace(interaction, 'editing reply');
    await interaction.editReply(payload);
    trace(interaction, 'edited reply');
  },
};
