'use strict';

const {
  SlashCommandBuilder,
  MessageFlags,
  ActionRowBuilder,
  RoleSelectMenuBuilder,
} = require('discord.js');
const { LOG_TYPES, LOG_DESCRIPTIONS } = require('../services/logChannels');
const { EMOJIS } = require('../services/reactionRoles');
const { errorEmbed, successEmbed } = require('../embeds');

const PREFIX = 'rr:role:';

const isOwner = (interaction, context) => interaction.user.id === context.config.ownerId;
const denied = () => ({
  embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
  flags: MessageFlags.Ephemeral,
});

// The panel the owner sees: one role picker per log (Discord allows 5 rows, we need exactly 5).
function panel(reactionRoles) {
  const lines = LOG_TYPES.map((t) => {
    const role = reactionRoles.roleFor(t.value);
    return `${EMOJIS[t.value]} **${t.name}** — ${role ? `<@&${role}>` : '*no role (DMs only)*'}`;
  });
  const embed = {
    color: 0x5865f2,
    title: '🎭 Reaction roles',
    description:
      'Roles are created automatically for every log; change which role each log uses here. Then run `/reactionrole post` to publish the reaction message.\n' +
      'Reacting gives the role **and** makes the bot DM that person when something is posted to the log.\n\n' +
      lines.join('\n') +
      '\n\n*Clear a picker to remove its role.*',
  };
  const components = LOG_TYPES.map((t) =>
    new ActionRowBuilder().addComponents(
      new RoleSelectMenuBuilder()
        .setCustomId(`${PREFIX}${t.value}`)
        .setPlaceholder(`${t.name} role`)
        .setMinValues(0)
        .setMaxValues(1),
    ),
  );
  return { embeds: [embed], components, flags: MessageFlags.Ephemeral };
}

function postedMessage(reactionRoles) {
  const lines = LOG_TYPES.map((t) => {
    const role = reactionRoles.roleFor(t.value);
    return `${EMOJIS[t.value]} **${t.name}** — ${LOG_DESCRIPTIONS[t.value]}${role ? ` (<@&${role}>)` : ''}`;
  });
  return {
    embeds: [
      {
        color: 0x5865f2,
        title: '🔔 Get notified',
        description:
          'React to choose what you want to hear about. You get the role and the bot **DMs you** when it happens (make sure your DMs are open). Remove the reaction to stop.\n\n' +
          lines.join('\n'),
      },
    ],
    allowedMentions: { parse: [] },
  };
}

module.exports = {
  data: new SlashCommandBuilder()
    .setName('reactionrole')
    .setDescription('Bot owner only: reaction roles that also subscribe people to log DMs.')
    .addSubcommand((s) => s.setName('set').setDescription('Choose the role for each log.'))
    .addSubcommand((s) =>
      s
        .setName('post')
        .setDescription('Post the reaction message.')
        .addStringOption((o) => o.setName('channel_id').setDescription('Channel to post it in').setRequired(true)),
    ),

  async execute(interaction, context) {
    if (!isOwner(interaction, context)) return interaction.reply(denied());
    const rr = context.reactionRoles;

    if (interaction.options.getSubcommand() === 'set') {
      await interaction.deferReply({ flags: MessageFlags.Ephemeral });
      await rr.ensureRoles(context.logChannels, context.config).catch(() => {});
      const { flags, ...body } = panel(rr);
      return interaction.editReply(body);
    }

    // post
    const channelId = interaction.options.getString('channel_id', true).trim().replace(/^<#(\d+)>$/, '$1');
    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    await rr.ensureRoles(context.logChannels, context.config).catch(() => {});
    let channel = null;
    try {
      channel = await interaction.client.channels.fetch(channelId);
    } catch {
      channel = null;
    }
    if (!channel || !channel.isTextBased()) {
      return interaction.editReply({ embeds: [errorEmbed('Channel Not Found', `I cannot see a text channel with ID \`${channelId}\`.`)] });
    }
    let message;
    try {
      message = await channel.send(postedMessage(rr));
      for (const t of LOG_TYPES) await message.react(EMOJIS[t.value]);
    } catch (error) {
      return interaction.editReply({
        embeds: [errorEmbed('Cannot Post There', `${error.message}\nThe bot needs View Channel, Send Messages, Embed Links and Add Reactions in <#${channelId}>.`)],
      });
    }
    await rr.setMessage(channel.id, message.id);
    return interaction.editReply({
      embeds: [successEmbed('Reaction message posted', `Posted in <#${channel.id}>. Re-run \`/reactionrole post\` after changing roles to refresh the text (the old message stops working).`)],
    });
  },

  /** Role pickers from /reactionrole set. */
  async handleComponent(interaction, context) {
    if (!isOwner(interaction, context)) return interaction.reply(denied());
    const type = interaction.customId.slice(PREFIX.length);
    if (!LOG_TYPES.some((t) => t.value === type)) return interaction.deferUpdate();
    const roleId = interaction.values[0] || null;
    try {
      await context.reactionRoles.setRole(type, roleId);
    } catch (error) {
      return interaction.reply({ embeds: [errorEmbed('Could Not Save', error.message)], flags: MessageFlags.Ephemeral });
    }
    return interaction.update(panel(context.reactionRoles));
  },
};
