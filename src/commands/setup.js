'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');
const { LOG_TYPES, labelFor } = require('../services/logChannels');
const { errorEmbed, successEmbed } = require('../embeds');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('setup')
    .setDescription('Bot owner only: configure the bot.')
    .addSubcommand((sub) =>
      sub
        .setName('log')
        .setDescription('Choose the channel for a log category.')
        .addStringOption((option) =>
          option
            .setName('action')
            .setDescription('Which log to set up.')
            .setRequired(true)
            .addChoices(...LOG_TYPES),
        )
        .addStringOption((option) =>
          option
            .setName('channel_id')
            .setDescription('ID of the channel that should receive it (a #channel mention also works).')
            .setRequired(true),
        ),
    ),

  async execute(interaction, { config, logChannels, tracker }) {
    // OWNER LOCK: nobody else can run this, in any server.
    if (interaction.user.id !== config.ownerId) {
      await interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
      return;
    }

    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    const fail = (title, text) => interaction.editReply({ embeds: [errorEmbed(title, text)] });

    const type = interaction.options.getString('action', true);
    const label = labelFor(type);
    if (!label) return fail('Unknown Log', 'That log type does not exist.');

    // Accept a plain ID or a <#id> mention.
    const channelId = interaction.options.getString('channel_id', true).trim().replace(/^<#(\d+)>$/, '$1');
    if (!/^\d{17,20}$/.test(channelId)) {
      return fail('Invalid Channel ID', 'A channel ID is 17-20 digits. In Discord: right-click the channel, then Copy Channel ID (turn on Developer Mode first).');
    }

    let channel = null;
    try {
      channel = await interaction.client.channels.fetch(channelId);
    } catch {
      channel = null;
    }
    if (!channel || !channel.isTextBased()) {
      return fail('Channel Not Found', `I cannot see a text channel with ID \`${channelId}\`. Make sure the bot is in that server and can view the channel.`);
    }

    // Post a short notice: this proves the bot can actually write there before we save anything.
    try {
      await channel.send({
        embeds: [successEmbed(`${label} channel set`, `This channel will now receive **${label}** posts.`)],
        allowedMentions: { parse: [] },
      });
    } catch {
      return fail('Cannot Send There', `I could not post in <#${channelId}>. Give the bot **View Channel**, **Send Messages** and **Embed Links** there, then try again.`);
    }

    const previous = logChannels.get(type);
    await logChannels.set(type, {
      channelId,
      guildId: channel.guildId || null,
      setBy: interaction.user.id,
      setAt: new Date().toISOString(),
    });

    const changed = previous && previous !== channelId ? `\nPreviously: <#${previous}>` : '';
    const roblox =
      type === 'platform_status'
        ? '\nThe live Roblox status message and outage alerts will be posted here.'
        : '';
    await interaction.editReply({
      embeds: [successEmbed(`${label} Set`, `**${label}** will go to <#${channelId}>.${roblox}${changed}`)],
    });

    // Post the live Roblox status message now instead of waiting for the next check.
    if (type === 'platform_status' && tracker) {
      tracker.poll().catch((error) => console.error(`[Setup] Immediate check failed: ${error.message}`));
    }
  },
};
