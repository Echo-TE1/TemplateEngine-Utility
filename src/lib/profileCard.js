'use strict';

// The Roblox profile card, built as a Discord "container" (Components V2): a heading, a divider,
// the details with the avatar on the right, and the buttons. Plain JSON, so it needs no discord.js.

const { trim, ts } = require('./format');

const IS_COMPONENTS_V2 = 1 << 15;
const EPHEMERAL = 1 << 6;
const ACCENT = 0x00a2ff;
const NA = 'Unavailable';

const num = (n) => (n == null ? NA : n.toLocaleString('en-US'));
const text = (content) => ({ type: 10, content });
const divider = () => ({ type: 14, divider: true, spacing: 1 });

function buttons(profile) {
  return {
    type: 1,
    components: [
      { type: 2, style: 5, label: 'View Profile', url: profile.url },
      { type: 2, style: 2, label: 'Refresh', custom_id: `rp:refresh:${profile.id}` },
    ],
  };
}

function wrap(children) {
  return {
    flags: IS_COMPONENTS_V2 | EPHEMERAL,
    components: [{ type: 17, accent_color: ACCENT, components: children }],
    allowedMentions: { parse: [] },
  };
}

/**
 * @param profile  from RobloxProfiles
 * @param emojis   optional { verified, premium, badges: [string|null per badge] } custom emoji markup
 */
function profileCard(profile, emojis = {}) {
  const p = profile;
  const r = p.rolimons;

  const marks = [];
  if (p.verified) marks.push(emojis.verified || '✓');
  if (r && r.premium && emojis.premium) marks.push(emojis.premium);
  const heading = `## [${p.name}](${p.url})${marks.length ? ` ${marks.join(' ')}` : ''}`;

  const lines = [];
  if (p.displayName && p.displayName !== p.name) lines.push(`**Display Name**: ${trim(p.displayName, 60)}`);
  if (p.banned) lines.push('**Account**: Terminated');
  lines.push(`**Following**: ${num(p.following)}`);
  lines.push(`**Followers**: ${num(p.followers)}`);
  const about = p.description.trim() ? trim(p.description.trim().replace(/\n{3,}/g, '\n\n'), 300) : 'No description';
  lines.push(`**Description**: ${about}`);
  lines.push('');

  // Badges: the icon when it could be uploaded as an emoji, otherwise the name.
  if (p.badges) {
    const icons = [];
    const names = [];
    p.badges.slice(0, 15).forEach((b, i) => {
      const icon = emojis.badges && emojis.badges[i];
      if (icon) icons.push(icon);
      else names.push(trim(b.name, 30));
    });
    const shown = [icons.join(' '), names.join(', ')].filter(Boolean).join(' ');
    lines.push(`**Badges (${p.badges.length})**: ${shown || 'None'}`);
  } else {
    lines.push(`**Badges**: ${NA}`);
  }

  lines.push(`**Created**: ${p.created ? ts(p.created, 'D') : NA}`);
  if (p.presence) lines.push(`**Status**: ${p.presence.status}`);
  let last = NA;
  if (p.presence && p.presence.lastOnline) last = ts(p.presence.lastOnline, 'D');
  else if (p.presence && p.presence.online) last = 'Online now';
  else if (r && r.lastOnline) last = ts(r.lastOnline, 'D');
  lines.push(`**Last Online**: ${last}`);
  lines.push('');

  const rolimonsUrl = `https://www.rolimons.com/player/${p.id}`;
  const link = (value) => (value == null ? NA : `[${num(value)}](${rolimonsUrl})`);
  lines.push(`**ID**: ${p.id}`);
  // RAP comes from Roblox's own inventory data (any public inventory). Value comes from Rolimons.
  const inv = p.inventory;
  let rap;
  if (inv && inv.private) rap = 'Private';
  else if (inv) rap = `${inv.complete ? '' : 'At least '}${num(inv.rap)}`;
  else rap = r && r.tracked !== false && r.rap != null ? link(r.rap) : NA; // inventory unreadable: fall back to Rolimons
  let value = NA;
  if (r) value = r.private ? 'Private' : r.tracked === false || r.value == null ? 'No data on Rolimons' : link(r.value);
  lines.push(`**RAP**: ${rap}`);
  lines.push(`**Value**: ${value}`);

  const body = text(trim(lines.join('\n'), 3500));
  const details = p.avatarUrl
    ? { type: 9, components: [body], accessory: { type: 11, media: { url: p.avatarUrl }, description: `${p.name}'s avatar` } }
    : body;

  const sources = ['Roblox', r && r.tracked !== false && r.value != null ? 'Rolimons' : null].filter(Boolean).join(' · ');
  const note = p.rateLimited ? ' · some data was rate limited, press Refresh later' : '';
  return wrap([text(heading), divider(), details, divider(), text(`-# ${sources}${note}`), buttons(p)]);
}

/** A problem (bad name, not found, rate limit, ...) shown in the same card style. */
function errorCard(title, description) {
  return wrap([text(`## ${trim(title, 100)}`), text(trim(description, 1000))]);
}

module.exports = { profileCard, errorCard, IS_COMPONENTS_V2, EPHEMERAL };
