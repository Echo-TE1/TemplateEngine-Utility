'use strict';

// Pure helpers for /channel create (ported from the old Python bot). No discord.js needed here.

const MINUTE = 60_000;
const MAX_MS = 3650 * 24 * 60 * MINUTE; // 10 years

/** "1m", "1h", "1d", "1w", "1mo", "1year" (and longer words) -> milliseconds, or null if invalid. */
function parseDuration(text) {
  const match = /^(\d+)\s*(years?|yrs?|y|months?|mos?|mo|weeks?|w|days?|d|hours?|hrs?|h|minutes?|mins?|m)$/.exec(
    String(text || '').trim().toLowerCase(),
  );
  if (!match) return null;
  const amount = Number.parseInt(match[1], 10);
  const unit = match[2];
  if (!(amount >= 1)) return null;
  let ms;
  if (unit.startsWith('y')) ms = 365 * 24 * 60 * MINUTE * amount;
  else if (unit.startsWith('mo')) ms = 30 * 24 * 60 * MINUTE * amount;
  else if (unit.startsWith('w')) ms = 7 * 24 * 60 * MINUTE * amount;
  else if (unit.startsWith('d')) ms = 24 * 60 * MINUTE * amount;
  else if (unit.startsWith('h')) ms = 60 * MINUTE * amount;
  else ms = MINUTE * amount;
  return ms <= MAX_MS ? ms : null;
}

/** Role mentions (<@&id>) or raw IDs -> { ids, bad } */
function parseRoleIds(text) {
  const ids = [];
  const bad = [];
  for (const token of String(text || '').trim().split(/[\s,]+/)) {
    if (!token) continue;
    const m = /^<@&(\d+)>$|^(\d{15,25})$/.exec(token);
    if (m) ids.push(m[1] || m[2]);
    else bad.push(token);
  }
  return { ids: [...new Set(ids)], bad };
}

// Option name -> [label shown in replies, discord.js permission name]. Basic permissions only.
// 6 other options + these 19 = Discord's limit of 25 options per command.
const PERMISSION_OPTIONS = {
  view_channel: ['View Channel', 'ViewChannel'],
  send_messages: ['Send Messages', 'SendMessages'],
  read_message_history: ['Read Message History', 'ReadMessageHistory'],
  embed_links: ['Embed Links', 'EmbedLinks'],
  attach_files: ['Attach Files', 'AttachFiles'],
  add_reactions: ['Add Reactions', 'AddReactions'],
  use_external_emojis: ['Use External Emojis', 'UseExternalEmojis'],
  use_external_stickers: ['Use External Stickers', 'UseExternalStickers'],
  create_public_threads: ['Create Public Threads', 'CreatePublicThreads'],
  send_messages_in_threads: ['Send Messages In Threads', 'SendMessagesInThreads'],
  use_application_commands: ['Use Application Commands', 'UseApplicationCommands'],
  send_polls: ['Send Polls', 'SendPolls'],
  send_voice_messages: ['Send Voice Messages', 'SendVoiceMessages'],
  connect: ['Connect', 'Connect'],
  speak: ['Speak', 'Speak'],
  stream: ['Stream', 'Stream'],
  use_voice_activation: ['Use Voice Activity', 'UseVAD'],
  use_soundboard: ['Use Soundboard', 'UseSoundboard'],
  request_to_speak: ['Request To Speak', 'RequestToSpeak'],
};

// What every allowed role gets by default (see, read, write, talk). Your choices win.
const DEFAULT_ROLE_PERMISSIONS = {
  ViewChannel: true,
  SendMessages: true,
  ReadMessageHistory: true,
  AddReactions: true,
  EmbedLinks: true,
  AttachFiles: true,
  Connect: true,
  Speak: true,
  UseVAD: true,
};

/** { optionName: true|false } -> { PermissionName: true|false } */
function toPermissionMap(chosen) {
  const out = {};
  for (const [key, value] of Object.entries(chosen)) out[PERMISSION_OPTIONS[key][1]] = value;
  return out;
}

const toLists = (map) => ({
  allow: Object.keys(map).filter((k) => map[k] === true),
  deny: Object.keys(map).filter((k) => map[k] === false),
});

/**
 * With allowed roles the channel is private: hidden from @everyone, and the chosen permissions go to those roles.
 * Without roles the chosen permissions apply to @everyone. The bot keeps access so it can delete the channel later.
 */
function buildOverwrites({ everyoneId, botId, roleIds, chosen }) {
  const permissions = toPermissionMap(chosen);
  const overwrites = [];
  if (roleIds.length) {
    overwrites.push({ id: everyoneId, deny: ['ViewChannel'] });
    const merged = toLists({ ...DEFAULT_ROLE_PERMISSIONS, ...permissions });
    for (const id of roleIds) overwrites.push({ id, ...merged });
  } else if (Object.keys(permissions).length) {
    overwrites.push({ id: everyoneId, ...toLists(permissions) });
  }
  if (overwrites.length) {
    overwrites.push({ id: botId, allow: ['ViewChannel', 'ManageChannels', 'SendMessages', 'Connect'] });
  }
  return overwrites;
}

module.exports = { parseDuration, parseRoleIds, PERMISSION_OPTIONS, DEFAULT_ROLE_PERMISSIONS, toPermissionMap, buildOverwrites };
