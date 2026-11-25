'use strict';

/**
 * A Discord message link (https://discord.com/channels/<guild>/<channel>/<message>) or a bare message ID
 * -> { guildId, channelId, messageId }. Bare IDs belong to the current channel (channelId null). Pure.
 */
function parseMessageRef(input) {
  const text = String(input || '').trim();
  const link = text.match(/^https?:\/\/(?:\w+\.)?(?:discord|discordapp)\.com\/channels\/(\d{15,25})\/(\d{15,25})\/(\d{15,25})\/?$/i);
  if (link) return { guildId: link[1], channelId: link[2], messageId: link[3] };
  if (/^\d{15,25}$/.test(text)) return { guildId: null, channelId: null, messageId: text };
  return null;
}

module.exports = { parseMessageRef };
