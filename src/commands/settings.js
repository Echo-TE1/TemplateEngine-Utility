'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');
const { successEmbed } = require('../embeds');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('settings')
    .setDescription('Your personal settings for this bot.')
    .addSubcommand((s) =>
      s
        .setName('mock')
        .setDescription('Allow or stop others making labelled mock-up images of you with /mock.')
        .addBooleanOption((o) => o.setName('allow').setDescription('On: others may use your name and picture in mock-ups. Off: nobody may.').setRequired(true))),

  async execute(interaction, context) {
    const on = interaction.options.getBoolean('allow', true);
    await context.mockConsent.set(interaction.user.id, on);
    console.log(`[Settings] ${interaction.user.id} set mock-ups of themselves to ${on ? 'ON' : 'OFF'}`);
    await interaction.reply({
      embeds: [successEmbed(on ? 'Mock-ups allowed' : 'Mock-ups off', on
        ? 'Others can now make mock-up images with your name and picture. Every image is labelled MOCK. Run this again with allow:off to stop it at any time.'
        : 'Nobody can make mock-up images of you.')],
      flags: MessageFlags.Ephemeral,
    });
  },
};
