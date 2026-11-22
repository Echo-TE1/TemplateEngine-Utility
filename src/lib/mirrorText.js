'use strict';

// Pure helpers that turn a Discord message into the embed posted in Bot Updates.
// Returns plain embed objects (discord.js accepts them), so this file needs no discord.js.

const MAX_DESCRIPTION = 4000;

function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Message-like object -> list of { name, url, isImage }. Works for arrays and Discord collections. */
function attachmentsOf(message) {
  const source = message.attachments;
  if (!source) return [];
  const items = typeof source.values === 'function' ? [...source.values()] : Array.isArray(source) ? source : [];
  return items
    .filter((a) => a && a.url)
    .map((a) => ({
      name: a.name || 'attachment',
      url: a.url,
      isImage: /^image\//i.test(a.contentType || '') || /\.(png|jpe?g|gif|webp)(\?|$)/i.test(a.url),
    }));
}

function mirrorEmbed(message, { edited = false } = {}) {
  const author = message.author || {};
  const files = attachmentsOf(message);
  const lines = [];
  if (message.content) lines.push(message.content);
  for (const f of files) lines.push(`📎 [${f.name}](${f.url})`);
  if (!lines.length && (message.embeds || []).length) lines.push('*(message contains an embed)*');

  const created = message.createdTimestamp ? new Date(message.createdTimestamp) : new Date();
  const embed = {
    color: edited ? 0xf0b232 : 0x5865f2,
    author: {
      name: author.tag || author.username || author.id || 'Unknown user',
      ...(typeof author.displayAvatarURL === 'function' ? { icon_url: author.displayAvatarURL() } : {}),
    },
    description: clip(lines.join('\n') || '*(empty message)*', MAX_DESCRIPTION),
    timestamp: created.toISOString(),
    footer: { text: edited ? 'Edited' : 'Message' },
  };
  const image = files.find((f) => f.isImage);
  if (image) embed.image = { url: image.url };
  if (message.url) embed.fields = [{ name: 'Original', value: `[Jump to message](${message.url})` }];
  return embed;
}

module.exports = { mirrorEmbed, attachmentsOf, clip };
