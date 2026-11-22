'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');
const { errorEmbed, successEmbed } = require('../embeds');
const { editReply } = require('../lib/respond');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('reactionrole')
    .setDescription('Bot owner only: set the channel for the reaction-role message.')
    .addSubcommand((s) =>
      s
        .setName('set')
        .setDescription('Post the reaction-role message in a channel (it is kept up to date automatically).')
        .addStringOption((o) => o.setName('channel_id').setDescription('Channel to post it in').setRequired(true)),
    ),

  async execute(interaction, context) {
    if (interaction.user.id !== context.config.ownerId) {
      return interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
    }
    const rr = context.reactionRoles;
    const channelId = interaction.options.getString('channel_id', true).trim().replace(/^<#(\d+)>$/, '$1');
    if (!/^\d{17,20}$/.test(channelId)) {
      return interaction.reply({ embeds: [errorEmbed('Invalid Channel ID', 'A channel ID is 17-20 digits.')], flags: MessageFlags.Ephemeral });
    }
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });

    let channel = null;
    try {
      channel = await interaction.client.channels.fetch(channelId);
    } catch {
      channel = null;
    }
    if (!channel || !channel.isTextBased()) {
      return editReply(interaction, { embeds: [errorEmbed('Channel Not Found', `I cannot see a text channel with ID \`${channelId}\`.`)] });
    }

    try {
      // Point at the new channel first so the roles are made in that server.
      const previous = rr.message;
      await rr.ensureRoles(context.logChannels, { ...context.config, mirrorChannelId: channelId }).catch(() => {});
      await rr.publish(channel);
      if (previous && previous.channelId !== channelId) {
        // Remove the old message so only one reaction message works.
        try {
          const oldChannel = await interaction.client.channels.fetch(previous.channelId);
          const old = await oldChannel.messages.fetch(previous.messageId);
          await old.delete();
        } catch {
          // already gone
        }
      }
    } catch (error) {
      return editReply(interaction, {
        embeds: [errorEmbed('Cannot Post There', `${error.message}\nThe bot needs View Channel, Send Messages, Embed Links and Add Reactions in <#${channelId}>.`)],
      });
    }
    return editReply(interaction, {
      embeds: [
        successEmbed(
          'Reaction message posted',
          `Posted in <#${channel.id}>. The bot keeps it up to date and re-posts it if it is deleted, also after a redeploy.\nRoles for every log are created automatically.`,
        ),
      ],
    });
  },
};
