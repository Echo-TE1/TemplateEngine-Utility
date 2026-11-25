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
    .addStringOption((o) =>
      o.setName('color').setDescription('Name colour').addChoices(...Object.keys(PALETTE).map((k) => ({ name: k, value: k })))),

  async execute(interaction) {
    const build = async () => {
      try {
        const text = interaction.options.getString('text', true);
        const given = interaction.options.getString('name');
        const color = interaction.options.getString('color') || 'blue';
        let name = given;
        let avatar = null;
        if (!given) {
          // Yourself: your own server name and picture.
          name = (interaction.member && interaction.member.displayName) || interaction.user.username;
          avatar = await fetch(interaction.user.displayAvatarURL({ extension: 'png', size: 128 }), { signal: AbortSignal.timeout(4000) })
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
