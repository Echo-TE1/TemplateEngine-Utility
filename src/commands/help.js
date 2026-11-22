'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');

const PUBLIC = [
  ['/robloxstatus', 'Current Roblox status. Pick a view for incident history or uptime.'],
  ['Search Roblox User', 'The button on the lookup panel: a private Roblox profile card.'],
];

const OWNER = [
  ['/setup log', 'Choose the channel for each log.'],
  ['/setup role', 'Role pinged for a service.'],
  ['/setup view / clear', 'See or remove log settings.'],
  ['/reactionrole set', 'Post the reaction-role message.'],
  ['/profile tracker panel', 'Post the Roblox lookup panel.'],
  ['/channel create', 'Create a channel, optionally temporary.'],
  ['/botstatus', 'Bot health and what each part is doing.'],
];

const lines = (rows) => rows.map(([name, text]) => `**${name}**\n${text}`).join('\n\n');

module.exports = {
  data: new SlashCommandBuilder().setName('help').setDescription('What this bot can do.'),

  async execute(interaction, context) {
    const owner = interaction.user.id === context.config.ownerId;
    const fields = [{ name: 'For everyone', value: lines(PUBLIC) }];
    if (owner) fields.push({ name: 'Owner only', value: lines(OWNER) });
    await interaction.reply({
      embeds: [
        {
          color: 0x5865f2,
          title: 'Roblox tracker',
          description: 'Watches Roblox and tells the server when something breaks, changes or recovers.',
          fields,
        },
      ],
      flags: MessageFlags.Ephemeral,
    });
  },
};
