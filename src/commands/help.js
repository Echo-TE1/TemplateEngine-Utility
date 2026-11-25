'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');

const PUBLIC = [['/settings mock', 'Allow or stop others making labelled mock-up images of you.']];
const OWNER = [];

const lines = (rows) => rows.map(([name, text]) => `**${name}**\n${text}`).join('\n\n');

module.exports = {
  data: new SlashCommandBuilder().setName('help').setDescription('What this bot can do.'),

  async execute(interaction, context) {
    const owner = interaction.user.id === context.config.ownerId;
    const embeds = [{ title: 'Commands', description: lines(owner ? [...OWNER, ...PUBLIC] : PUBLIC), color: 0x5865f2 }];
    await interaction.reply({ embeds, flags: MessageFlags.Ephemeral });
  },
};
