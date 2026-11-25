'use strict';

const { SlashCommandBuilder } = require('discord.js');
const { errorEmbed } = require('../embeds');
const { respondWhenReady } = require('../lib/respond');
const { parseMessageRef } = require('../lib/messageRef');
const { renderMessageCard } = require('../lib/messageCard');

const MAX_CHARS = 500;

module.exports = {
  data: new SlashCommandBuilder()
    .setName('quote')
    .setDescription('Make a quote card from a real message in this server.')
    .setDMPermission(false)
    .addStringOption((o) => o.setName('message').setDescription('Message link, or the message ID if it is in this channel').setRequired(true)),

  async execute(interaction) {
    const build = async () => {
      try {
        const ref = parseMessageRef(interaction.options.getString('message', true));
        if (!ref) return { embeds: [errorEmbed('Invalid Message', 'Give a message link (Copy Message Link) or a message ID.')] };
        if (ref.guildId && ref.guildId !== interaction.guildId) return { embeds: [errorEmbed('Other Server', 'Only messages from this server can be quoted.')] };
        const channel = ref.channelId ? await interaction.guild.channels.fetch(ref.channelId).catch(() => null) : interaction.channel;
        if (!channel || !channel.isTextBased()) return { embeds: [errorEmbed('Channel Not Found', 'I cannot see that channel.')] };
        const message = await channel.messages.fetch(ref.messageId).catch(() => null);
        if (!message) return { embeds: [errorEmbed('Message Not Found', 'I could not read that message. Check the link and that I can see the channel.')] };
        if (!message.content) {
          return { embeds: [errorEmbed('No Text To Quote', 'That message has no text, or I am not allowed to read message text (Message Content intent).')] };
        }
        const cut = message.content.length > MAX_CHARS;
        const text = cut ? `${message.content.slice(0, MAX_CHARS - 3)}...` : message.content;
        const name = (message.member && message.member.displayName) || message.author.username;
        const avatar = await fetch(message.author.displayAvatarURL({ extension: 'png', size: 128 }), { signal: AbortSignal.timeout(4000) })
          .then((r) => (r.ok ? r.arrayBuffer() : null))
          .then((b) => (b ? Buffer.from(b) : null))
          .catch(() => null);
        const image = await renderMessageCard({
          name,
          text,
          avatar,
          time: message.createdAt.toISOString().slice(0, 16).replace('T', ' ') + ' UTC',
          footer: `Quoted from #${channel.name}${cut ? ' (shortened)' : ''}`,
        });
        return { content: `Original: ${message.url}`, files: [{ attachment: image, name: 'quote.png' }], allowedMentions: { parse: [] } };
      } catch (error) {
        console.error(`[Cmd] /quote failed: ${error.stack || error.message}`);
        return { embeds: [errorEmbed('Quote Failed', `\`${error.message}\``)] };
      }
    };
    await respondWhenReady(interaction, build, { quickMs: 0 });
  },
};
