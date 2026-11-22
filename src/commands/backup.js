'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');
const { errorEmbed, successEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');
const { buildBackup, toAttachment } = require('../services/backup');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('backup')
    .setDescription('Bot owner only: download everything the bot remembers (incident history, log channels, roles).'),

  async execute(interaction, { config, store, logChannels, serviceRoles }) {
    if (interaction.user.id !== config.ownerId) {
      await interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
      return;
    }

    const build = async () => {
      try {
        const backup = await buildBackup({ store, logChannels, serviceRoles });
        const kb = Math.round(backup.buffer.length / 1024);
        return {
          embeds: [
            successEmbed(
              '💾 Backup ready',
              `${backup.counts.incidents} incident(s), ${backup.counts.logs} log channel(s), ${backup.counts.roles} role(s) • ${kb} KB`,
            ),
          ],
          files: [toAttachment(backup)],
        };
      } catch (error) {
        console.error(`[Cmd] /backup failed: ${error.stack || error.message}`);
        return { embeds: [errorEmbed('Backup Failed', `\`${error.message}\``)] };
      }
    };

    await respondWhenReady(interaction, build, { ephemeral: true });
  },
};
