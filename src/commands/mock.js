'use strict';

const { SlashCommandBuilder } = require('discord.js');
const { errorEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');
const { renderMessageCard, PALETTE } = require('../lib/messageCard');

module.exports = {
  data: new SlashCommandBuilder()
    .setName('mock')
    .setDescription('Make a clearly labelled mock-up chat message (invented name, or yourself).')
    .setDMPermission(false)
    .addStringOption((o) => o.setName('text').setDescription('What the message says').setRequired(true).setMaxLength(400))
    .addStringOption((o) => o.setName('name').setDescription('Invented character name (leave empty to use yourself)').setMaxLength(32))
    .addUserOption((o) => o.setName('user').setDescription('Someone who turned mock-ups on in /settings (they can turn it off any time)'))
    .addStringOption((o) =>
      o.setName('color').setDescription('Name colour').addChoices(...Object.keys(PALETTE).map((k) => ({ name: k, value: k })))),

  async execute(interaction, context) {
    const build = async () => {
      try {
        const text = interaction.options.getString('text', true);
        const given = interaction.options.getString('name');
        const color = interaction.options.getString('color') || 'blue';
        const target = interaction.options.getUser('user');
        if (target && given) return { embeds: [errorEmbed('Pick One', 'Use either a user or an invented name, not both.')] };
        if (target && target.bot) return { embeds: [errorEmbed('Not Allowed', 'Mock-ups of bots are not supported.')] };
        if (target && target.id !== interaction.user.id && !context.mockConsent.isAllowed(target.id)) {
          return { embeds: [errorEmbed('Not Allowed', `${target.username} has not turned mock-ups on. They can allow it with \`/settings mock allow:on\`.`)] };
        }
        const subject = target || (given ? null : interaction.user);
        let name = given;
        let avatar = null;
        if (subject) {
          console.log(`[Mock] ${interaction.user.id} made a labelled mock-up of ${subject.id}`);
          const member = await interaction.guild.members.fetch(subject.id).catch(() => null);
          name = (member && member.displayName) || subject.username;
          avatar = await fetch(subject.displayAvatarURL({ extension: 'png', size: 128 }), { signal: AbortSignal.timeout(4000) })
            .then((r) => (r.ok ? r.arrayBuffer() : null))
            .then((b) => (b ? Buffer.from(b) : null))
            .catch(() => null);
        } else {
          // An invented name must not be a real member's, so nobody can be made to "say" something.
          const clash = await interaction.guild.members.search({ query: given, limit: 5 }).catch(() => null);
          const lower = given.toLowerCase();
          if (clash && clash.some((m) => m.displayName.toLowerCase() === lower || m.user.username.toLowerCase() === lower)) {
            return { embeds: [errorEmbed('Name In Use', 'That is a real member of this server. Pick an invented name, or leave the name empty to mock up yourself.')] };
          }
        }
        const image = await renderMessageCard({ name, text, color, avatar, time: 'Today' }, { watermark: true });
        return { files: [{ attachment: image, name: 'mock.png' }], allowedMentions: { parse: [] } };
      } catch (error) {
        console.error(`[Cmd] /mock failed: ${error.stack || error.message}`);
        return { embeds: [errorEmbed('Mock-up Failed', `\`${error.message}\``)] };
      }
    };
    await respondWhenReady(interaction, build, { quickMs: 0 });
  },
};
