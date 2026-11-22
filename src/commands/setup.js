'use strict';

const { SlashCommandBuilder, MessageFlags } = require('discord.js');
const config = require('../config');
const { LOG_TYPES, LOG_DESCRIPTIONS, labelFor } = require('../services/logChannels');
const { ALL } = require('../services/serviceRoles');
const { errorEmbed, successEmbed, logViewEmbed } = require('../embeds');
const { respondWhenReady, trace } = require('../lib/respond');

// "All alerts" plus each tracked service (Discord allows at most 25 choices).
const ROLE_SERVICES = [{ name: 'All alerts', value: ALL }, ...config.trackedServices.slice(0, 24).map((s) => ({ name: s, value: s }))];

const fail = (title, text) => ({ embeds: [errorEmbed(title, text)] });

// ---------------------------------------------------------------------------------------
// /setup log
// ---------------------------------------------------------------------------------------
async function buildLog(interaction, context, flags) {
  const { logChannels } = context;
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
  trace(interaction, 'channel found');

  // Post a short notice: this proves the bot can actually write there before we save anything.
  try {
    await channel.send({
      embeds: [successEmbed(`${label} channel set`, `This channel will now receive **${label}** posts.`)],
      allowedMentions: { parse: [] },
    });
  } catch {
    return fail('Cannot Send There', `I could not post in <#${channelId}>. Give the bot **View Channel**, **Send Messages** and **Embed Links** there, then try again.`);
  }
  trace(interaction, 'notice posted, saving');

  const previous = logChannels.get(type);
  await logChannels.set(type, {
    channelId,
    guildId: channel.guildId || null,
    setBy: interaction.user.id,
    setAt: new Date().toISOString(),
  });
  trace(interaction, 'saved');
  if (context.reactionRoles) {
    context.reactionRoles.ensureRoles(logChannels, context.config).catch((e) => console.warn(`[ReactionRoles] ${e.message}`));
  }

  const changed = previous && previous !== channelId ? `\nPreviously: <#${previous}>` : '';
  const extra = LOG_DESCRIPTIONS[type] ? `\n${LOG_DESCRIPTIONS[type]}.` : '';
  flags.startRobloxCheck = type === 'platform_status';
  return { embeds: [successEmbed(`${label} Set`, `**${label}** will go to <#${channelId}>.${extra}${changed}`)] };
}

// ---------------------------------------------------------------------------------------
// /setup view
// ---------------------------------------------------------------------------------------
async function buildView(interaction, { logChannels, serviceRoles }) {
  const rows = LOG_TYPES.map((t) => ({
    label: t.name,
    description: LOG_DESCRIPTIONS[t.value] || '',
    entry: logChannels.entry(t.value),
  }));
  return { embeds: [logViewEmbed(rows, serviceRoles.list())] };
}

// ---------------------------------------------------------------------------------------
// /setup clear
// ---------------------------------------------------------------------------------------
async function buildClear(interaction, { logChannels }) {
  const type = interaction.options.getString('action', true);
  const label = labelFor(type);
  if (!label) return fail('Unknown Log', 'That log type does not exist.');

  const had = await logChannels.clear(type);
  if (!had) return fail('Nothing To Clear', `**${label}** was not set up.`);
  const note =
    type === 'platform_status'
      ? '\nThe live status message stops updating (the old message stays in the channel until you delete it).'
      : '';
  return { embeds: [successEmbed(`${label} Cleared`, `**${label}** will no longer be posted anywhere.${note}`)] };
}

// ---------------------------------------------------------------------------------------
// /setup role
// ---------------------------------------------------------------------------------------
async function buildRole(interaction, { serviceRoles }) {
  const service = interaction.options.getString('service', true);
  const name = service === ALL ? 'All alerts' : service;
  const role = interaction.options.getRole('role');

  if (!role) {
    const had = await serviceRoles.clear(service);
    return had
      ? { embeds: [successEmbed('Role Removed', `**${name}** no longer pings a role.`)] }
      : fail('Nothing To Remove', `No role was set for **${name}**. Pick a \`role\` to set one.`);
  }
  if (role.id === interaction.guildId) {
    return fail('Not Allowed', 'Pick a real role, not @everyone.');
  }

  await serviceRoles.set(service, role.id);
  return {
    embeds: [
      successEmbed(
        'Role Set',
        `Incident alerts for **${name}** will ping <@&${role.id}>.\n` +
          'Pings only work if the role is mentionable, or the bot has the **Mention @everyone, @here and All Roles** permission.',
      ),
    ],
  };
}

const BUILDERS = { log: buildLog, view: buildView, clear: buildClear, role: buildRole };

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
    )
    .addSubcommand((sub) => sub.setName('view').setDescription('Show which channel each log uses, and the alert roles.'))
    .addSubcommand((sub) =>
      sub
        .setName('clear')
        .setDescription('Stop posting a log category.')
        .addStringOption((option) =>
          option
            .setName('action')
            .setDescription('Which log to clear.')
            .setRequired(true)
            .addChoices(...LOG_TYPES),
        ),
    )
    .addSubcommand((sub) =>
      sub
        .setName('role')
        .setDescription('Choose the role pinged for incidents on a service (leave role empty to remove it).')
        .addStringOption((option) =>
          option
            .setName('service')
            .setDescription('Which service.')
            .setRequired(true)
            .addChoices(...ROLE_SERVICES),
        )
        .addRoleOption((option) => option.setName('role').setDescription('The role to ping.')),
    ),

  async execute(interaction, context) {
    // OWNER LOCK: nobody else can run this, in any server.
    if (interaction.user.id !== context.config.ownerId) {
      await interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
      return;
    }

    const flags = { startRobloxCheck: false };
    const sub = interaction.options.getSubcommand();
    const builder = BUILDERS[sub];

    // Never throws: a problem becomes a message instead of a hung command.
    const build = async () => {
      try {
        if (!builder) return fail('Unknown Command', `/setup ${sub} does not exist.`);
        return await builder(interaction, context, flags);
      } catch (error) {
        console.error(`[Cmd] /setup ${sub} failed: ${error.stack || error.message}`);
        return fail('Setup Failed', `Something went wrong while saving.\n\`${error.message}\``);
      }
    };

    await respondWhenReady(interaction, build, { ephemeral: true });

    // Post the live Roblox status message now instead of waiting for the next check.
    if (flags.startRobloxCheck && context.tracker) {
      context.tracker.poll().catch((error) => console.error(`[Setup] Immediate check failed: ${error.message}`));
    }
  },
};
