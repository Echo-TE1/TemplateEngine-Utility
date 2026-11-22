'use strict';

const { SlashCommandBuilder, MessageFlags, ChannelType } = require('discord.js');
const { errorEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');
const {
  parseDuration,
  parseRoleIds,
  PERMISSION_OPTIONS,
  buildOverwrites,
} = require('../lib/channelOptions');

const KINDS = {
  chat: { label: 'Chat', type: ChannelType.GuildText },
  voice: { label: 'Voice', type: ChannelType.GuildVoice },
  announcement: { label: 'Announcement', type: ChannelType.GuildAnnouncement },
  stage: { label: 'Stage', type: ChannelType.GuildStageVoice },
  forum: { label: 'Forum', type: ChannelType.GuildForum },
};

const code = (v) => `\`${v}\``;
const fail = (title, text) => ({ embeds: [errorEmbed(title, text)] });

const data = new SlashCommandBuilder()
  .setName('channel')
  .setDescription('Bot owner only: create channels.')
  .setDMPermission(false)
  .addSubcommand((sub) => {
    sub
      .setName('create')
      .setDescription('Create a channel. Permission options: true = allow, false = deny.')
      .addStringOption((o) => o.setName('name').setDescription('Channel name.').setRequired(true).setMaxLength(100))
      .addStringOption((o) =>
        o
          .setName('action')
          .setDescription('What kind of channel to create.')
          .setRequired(true)
          .addChoices(...Object.entries(KINDS).map(([value, k]) => ({ name: k.label, value }))),
      )
      .addStringOption((o) =>
        o
          .setName('type')
          .setDescription('Normal stays forever. Temporary is deleted automatically.')
          .setRequired(true)
          .addChoices({ name: 'Normal', value: 'normal' }, { name: 'Temporary', value: 'temporary' }),
      )
      .addChannelOption((o) =>
        o.setName('category').setDescription('Category to put the channel in (optional).').addChannelTypes(ChannelType.GuildCategory),
      )
      .addStringOption((o) =>
        o.setName('allowed_roles').setDescription('Roles that get access (mention them or paste IDs). Empty = permissions apply to @everyone.'),
      )
      .addStringOption((o) => o.setName('duration').setDescription('Temporary only: 1m, 1h, 1d, 1w, 1mo or 1year.'));
    for (const [key, [label]] of Object.entries(PERMISSION_OPTIONS)) {
      sub.addBooleanOption((o) => o.setName(key).setDescription(`${label}: true = allow, false = deny, empty = default.`));
    }
    return sub;
  });


async function build(interaction, context) {
  const guild = interaction.guild;
  const o = interaction.options;
  if (!guild) return fail('Server Only', 'Use this command in a server.');
  const me = guild.members.me || (await guild.members.fetchMe());
  if (!me.permissions.has('ManageChannels')) return fail('Missing Permissions', 'I need the **Manage Channels** permission in this server.');

  const name = o.getString('name', true).trim();
  if (!name) return fail('Invalid Name', 'The channel name cannot be empty.');
  const kind = KINDS[o.getString('action', true)];
  const temporary = o.getString('type', true) === 'temporary';
  const duration = o.getString('duration');
  const category = o.getChannel('category');

  let deleteAt = null;
  if (temporary) {
    if (!duration) return fail('Duration Missing', 'Temporary channels need a `duration`, for example `1h`, `1d`, `1w`, `1mo` or `1year`.');
    const ms = parseDuration(duration);
    if (ms === null) return fail('Invalid Duration', 'Use a number with one unit: `1m`, `1h`, `1d`, `1w`, `1mo` or `1year`. The maximum is 10 years.');
    deleteAt = Date.now() + ms;
  }

  // Allowed roles (mentions or IDs)
  const roles = [];
  const rolesText = o.getString('allowed_roles');
  if (rolesText && rolesText.trim()) {
    const { ids, bad } = parseRoleIds(rolesText);
    if (bad.length || !ids.length) {
      return fail('Invalid Roles', `I could not read: ${bad.map(code).join(', ') || '`nothing`'}\nUse role mentions or role IDs, separated by spaces or commas.`);
    }
    if (ids.length > 20) return fail('Too Many Roles', 'Please use 20 roles or fewer.');
    const missing = [];
    for (const id of ids) {
      const role = guild.roles.cache.get(id) || (await guild.roles.fetch(id).catch(() => null));
      if (!role) missing.push(code(id));
      else if (role.id === guild.id) return fail('Invalid Role', '`@everyone` cannot be an allowed role. Leave `allowed_roles` empty to set permissions for everyone.');
      else roles.push(role);
    }
    if (missing.length) return fail('Roles Not Found', `These roles are not in this server: ${missing.join(', ')}`);
  }

  // true = allow, false = deny, empty = leave as default
  const chosen = {};
  for (const key of Object.keys(PERMISSION_OPTIONS)) {
    const value = o.getBoolean(key);
    if (value !== null) chosen[key] = value;
  }
  const permissionOverwrites = buildOverwrites({
    everyoneId: guild.id,
    botId: me.id,
    roleIds: roles.map((r) => r.id),
    chosen,
  });

  let channel;
  try {
    channel = await guild.channels.create({
      name,
      type: kind.type,
      parent: category ? category.id : undefined,
      permissionOverwrites,
      reason: `Created by ${interaction.user.tag} (${interaction.user.id})`,
    });
  } catch (error) {
    if (error.code === 50013) {
      return fail('Missing Permissions', 'I could not create the channel. I need **Manage Channels**, and I can only give permissions that I have myself.');
    }
    const hint = ['announcement', 'stage', 'forum'].includes(o.getString('action')) ? '\nThis channel type needs **Community** to be enabled in the server settings.' : '';
    return fail('Could Not Create Channel', `Discord returned: \`${String(error.message).slice(0, 400)}\`${hint}`);
  }

  const mentionRoles = roles.map((r) => `<@&${r.id}>`).join(' ');
  const fields = [
    { name: 'Name', value: code(channel.name), inline: true },
    { name: 'Kind', value: code(kind.label), inline: true },
    { name: 'Type', value: code(temporary ? 'Temporary' : 'Normal'), inline: true },
    { name: 'Channel ID', value: code(channel.id), inline: true },
    { name: 'Category', value: category ? code(category.name) : '`None`', inline: true },
    { name: 'Allowed Roles', value: mentionRoles || '`Everyone`', inline: true },
  ];
  const keys = Object.keys(chosen);
  if (keys.length) {
    const names = (want) => keys.filter((k) => chosen[k] === want).map((k) => code(PERMISSION_OPTIONS[k][0])).join(' ') || '`None`';
    fields.push(
      { name: 'Permissions Apply To', value: mentionRoles || '`@everyone`' },
      { name: 'Allowed', value: names(true) },
      { name: 'Denied', value: names(false) },
    );
  }

  if (temporary) {
    const unix = Math.floor(deleteAt / 1000);
    try {
      await context.tempChannels.add(channel, deleteAt);
      fields.push({ name: 'Deletes', value: `<t:${unix}:R> (<t:${unix}:F>)` });
      if (!context.tempChannels.persistent) {
        fields.push({ name: 'Warning', value: '`MONGO_URI` is not set, so this timer is lost if the bot restarts. Add `MONGO_URI` on Render.' });
      }
    } catch (error) {
      console.error(`[TempChannels] Could not save timer for ${channel.id}: ${error.message}`);
      fields.push({ name: 'Warning', value: 'The channel was created, but I could not save its timer. It will not be deleted automatically.' });
    }
  } else if (duration) {
    fields.push({ name: 'Note', value: '`duration` is only used for temporary channels, so it was ignored.' });
  }

  return { embeds: [{ color: 0x57f287, title: 'Channel Created', description: `<#${channel.id}> was created.`, fields, timestamp: new Date().toISOString() }] };
}

module.exports = {
  data,

  async execute(interaction, context) {
    if (interaction.user.id !== context.config.ownerId) {
      await interaction.reply({
        embeds: [errorEmbed('Permission Denied', 'This command can only be used by the bot owner.')],
        flags: MessageFlags.Ephemeral,
      });
      return;
    }
    await respondWhenReady(
      interaction,
      async () => {
        try {
          return await build(interaction, context);
        } catch (error) {
          console.error(`[Cmd] /channel failed: ${error.stack || error.message}`);
          return fail('Channel Command Failed', `\`${error.message}\``);
        }
      },
      { ephemeral: true },
    );
  },
};
