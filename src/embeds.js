'use strict';

const { EmbedBuilder } = require('discord.js');
const { meta, worst } = require('./lib/levels');
const { trim, ts, duration, stateLabel } = require('./lib/format');

const COLOR_RED = 0xf43f5e;
const COLOR_GREY = 0x6b7280;

const field = (name, value, inline = false) => ({ name, value: trim(value || '—', 1024), inline });

function servicesText(item) {
  const names = item.services && item.services.length ? item.services.join(', ') : 'Not specified';
  return item.groups && item.groups.length ? `${names} (${item.groups.join(', ')})` : names;
}

// ---------------------------------------------------------------------------------------
// Live status message
// ---------------------------------------------------------------------------------------
function trackedLines(snap) {
  return snap.tracking.terms.map((term) => {
    const keys = snap.tracking.matches[term] || [];
    const matched = snap.services.filter((s) => keys.includes(s.key));

    if (matched.length) {
      const level = worst(matched.map((s) => s.level));
      const m = meta(level);
      if (level === 'operational') return `${m.mark} **${term}** — Operational`;
      const where = [...new Set(matched.filter((s) => s.level === level).map((s) => s.group))];
      return `${m.mark} **${term}** — ${m.label} (${where.join(', ')})`;
    }

    // Not a component on Roblox's status page: judged by the incidents that mention it.
    const mentioned = snap.incidents.some((i) => i.trackedTerms.includes(term));
    return mentioned
      ? `! **${term}** — Incident reported`
      : `✓ **${term}** — No incidents reported`;
  });
}

function boardEmbed(snap, ctx, warning = null) {
  const overall = meta(snap.overall.level);
  const headline =
    snap.overall.level === 'operational'
      ? 'All Systems Operational'
      : snap.overall.text || overall.label;

  const embed = new EmbedBuilder()
    .setTitle('Roblox Status')
    .setURL(ctx.statusPageUrl)
    .setColor(overall.color)
    .setDescription(`${overall.mark} **${headline}**${warning ? `\n\n! ${warning}` : ''}`)
    .addFields(field('Tracked Services', trackedLines(snap).join('\n')));

  if (snap.incidents.length) {
    const text = snap.incidents
      .slice(0, 5)
      .map((i) => {
        const latest = i.messages[i.messages.length - 1];
        return (
          `${meta(i.level).mark} **${trim(i.name, 80)}**\n` +
          `${stateLabel(latest && latest.state)} • started ${ts(i.openedAt, 'R')} • ${servicesText(i)}`
        );
      })
      .join('\n\n');
    const more = snap.incidents.length > 5 ? `\n\n…and ${snap.incidents.length - 5} more` : '';
    embed.addFields(field(`Active Incidents (${snap.incidents.length})`, text + more));
  } else {
    embed.addFields(field('Active Incidents', 'None'));
  }

  const maintenance = [...snap.maintenance.active, ...snap.maintenance.upcoming].slice(0, 3);
  if (maintenance.length) {
    const text = maintenance
      .map((m) => `~ **${trim(m.name, 80)}**\n${ts(m.plannedStart, 'f')} → ${ts(m.plannedEnd, 't')}`)
      .join('\n\n');
    embed.addFields(field('Maintenance', text));
  }

  // When anything is not fine, show exactly what is broken AND what is still working.
  const broken = snap.services.filter((s) => s.level !== 'operational');
  if (broken.length) {
    const shown = broken
      .slice(0, 15)
      .map((s) => `${meta(s.level).mark} **${s.group} / ${s.name}** — ${meta(s.level).label}`);
    const more = broken.length > 15 ? `\n…and ${broken.length - 15} more` : '';
    embed.addFields(field(`Not Operational (${broken.length})`, shown.join('\n') + more));

    const working = snap.services.filter((s) => s.level === 'operational');
    const byGroup = new Map();
    for (const s of working) byGroup.set(s.group, [...(byGroup.get(s.group) || []), s.name]);
    const lines = [...byGroup].map(([group, names]) => `✓ **${group}**: ${names.join(', ')}`);
    embed.addFields(field(`Operational (${working.length})`, lines.join('\n') || 'None'));
  }

  return embed
    .setFooter({ text: `Updates automatically • Checked every ${ctx.pollSeconds}s` })
    .setTimestamp(new Date(snap.fetchedAt));
}

// ---------------------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------------------
function incidentOpenEmbed(incident, ctx) {
  const m = meta(incident.level);
  const latest = incident.messages[incident.messages.length - 1];
  return new EmbedBuilder()
    .setTitle(trim(`${m.mark} Roblox Incident: ${incident.name}`, 256))
    .setURL(ctx.statusPageUrl)
    .setColor(m.color)
    .setDescription(trim((latest && latest.details) || 'No details have been posted yet.', 1800))
    .addFields(
      field('Status', stateLabel(latest && latest.state), true),
      field('Impact', m.label, true),
      field('Started', `${ts(incident.openedAt, 'F')}\n${ts(incident.openedAt, 'R')}`, true),
      field('Affected Services', servicesText(incident)),
    )
    .setFooter({ text: `Incident ${incident.id}` })
    .setTimestamp(new Date());
}

function incidentUpdateEmbed(incident, updates, ctx) {
  const m = meta(incident.level);
  const body = updates
    .map((u) => `**${stateLabel(u.state)}** — ${ts(u.datetime, 'R')}\n${trim(u.details, 900)}`)
    .join('\n\n');
  return new EmbedBuilder()
    .setTitle(trim(`Update: ${incident.name}`, 256))
    .setURL(ctx.statusPageUrl)
    .setColor(m.color)
    .setDescription(trim(body || 'The incident was updated.', 3000))
    .addFields(
      field('Impact', m.label, true),
      field('Started', ts(incident.openedAt, 'R'), true),
      field('Affected Services', servicesText(incident)),
    )
    .setFooter({ text: `Incident ${incident.id}` })
    .setTimestamp(new Date());
}

function incidentResolvedEmbed(record, resolvedAt, ctx) {
  const started = Date.parse(record.openedAt);
  const last = record.updates && record.updates[record.updates.length - 1];
  return new EmbedBuilder()
    .setTitle(trim(`Resolved: ${record.name}`, 256))
    .setURL(ctx.statusPageUrl)
    .setColor(meta('operational').color)
    .setDescription(
      trim(
        `Roblox reports this incident as resolved.${last && last.details ? `\n\n**Last update**\n${last.details}` : ''}`,
        1800,
      ),
    )
    .addFields(
      field('Duration', Number.isFinite(started) ? duration(Date.parse(resolvedAt) - started) : 'Unknown', true),
      field('Started', ts(record.openedAt, 'F'), true),
      field('Resolved', ts(resolvedAt, 'F'), true),
      field('Affected Services', servicesText(record)),
    )
    .setFooter({ text: `Incident ${record.id}` })
    .setTimestamp(new Date(resolvedAt));
}

function serviceChangeEmbed(changes, ctx) {
  const allRecovered = changes.every((c) => c.to === 'operational');
  const level = worst(changes.map((c) => c.to));
  const lines = changes
    .slice(0, 20)
    .map((c) => `${meta(c.to).mark} **${c.group} / ${c.name}** — ${meta(c.from).label} → ${meta(c.to).label}`);
  return new EmbedBuilder()
    .setTitle(allRecovered ? 'Roblox services recovered' : 'Roblox service status changed')
    .setURL(ctx.statusPageUrl)
    .setColor(allRecovered ? meta('operational').color : meta(level).color)
    .setDescription(trim(lines.join('\n'), 3500))
    .setTimestamp(new Date());
}

// ---------------------------------------------------------------------------------------
// History + errors
// ---------------------------------------------------------------------------------------
function historyEmbed(records, ctx) {
  const embed = new EmbedBuilder()
    .setTitle('Roblox incident history')
    .setURL(ctx.statusPageUrl)
    .setColor(COLOR_GREY)
    .setTimestamp(new Date());

  if (!records.length) {
    return embed.setDescription('No finished incidents have been recorded yet.');
  }

  const text = records
    .map(
      (r) =>
        `**${trim(r.name, 90)}**\n${servicesText(r)}\n` +
        `${ts(r.openedAt, 'f')} • lasted ${r.durationMs == null ? 'unknown' : duration(r.durationMs)}`,
    )
    .join('\n\n');
  return embed.setDescription(trim(text, 4000));
}

function errorEmbed(title, description) {
  return new EmbedBuilder().setTitle(title).setDescription(description).setColor(COLOR_RED);
}

function successEmbed(title, description) {
  return new EmbedBuilder().setTitle(title).setDescription(description).setColor(meta('operational').color);
}

// ---------------------------------------------------------------------------------------
// Direct health checks, news, bot notices
// ---------------------------------------------------------------------------------------
const hostOf = (url) => {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
};
const isUrl = (s) => /^https?:\/\/\S+$/i.test(String(s || ''));

function healthEmbed({ target, change, result, state }) {
  const host = hostOf(target.url);
  if (change === 'down') {
    const reason = result.error || (result.httpStatus ? `HTTP ${result.httpStatus}` : 'no answer');
    return new EmbedBuilder()
      .setTitle(trim(`${target.name} is not responding`, 256))
      .setColor(meta('outage').color)
      .setDescription(
        `Our own check of \`${host}\` failed ${state.fails} times in a row.\n` +
          'Roblox may not have posted about it on its status page yet.',
      )
      .addFields(
        field('Last result', reason, true),
        field('Detected', ts(state.downSince, 'f'), true),
        field('Checked URL', target.url),
      )
      .setTimestamp(new Date());
  }
  const since = Date.parse(state.lastDownSince);
  return new EmbedBuilder()
    .setTitle(trim(`${target.name} is back`, 256))
    .setColor(meta('operational').color)
    .setDescription(`\`${host}\` is answering again.`)
    .addFields(
      field('Was down for', Number.isFinite(since) ? duration(Date.parse(result.at) - since) : 'Unknown', true),
      field('Response time', result.ms == null ? '—' : `${result.ms} ms`, true),
    )
    .setTimestamp(new Date());
}

function newsEmbed(feed, item) {
  const embed = new EmbedBuilder()
    .setTitle(trim(item.title, 256))
    .setColor(0x00a2ff)
    .setAuthor({ name: trim(feed.name, 256) });
  if (isUrl(item.link)) embed.setURL(item.link);
  if (item.summary) embed.setDescription(trim(item.summary, 400));
  if (isUrl(item.image)) embed.setImage(item.image);
  if (item.tags && item.tags.length) embed.setFooter({ text: trim(item.tags.join(' • '), 200) });
  const published = new Date(item.published);
  return embed.setTimestamp(Number.isFinite(published.getTime()) ? published : new Date());
}

const NOTICE_COLORS = { ok: meta('operational').color, warn: meta('degraded').color, bad: meta('outage').color };

function botNoticeEmbed(title, description, level = 'ok') {
  return new EmbedBuilder()
    .setTitle(trim(title, 256))
    .setColor(NOTICE_COLORS[level] || NOTICE_COLORS.ok)
    .setDescription(trim(description, 3500))
    .setTimestamp(new Date());
}

// ---------------------------------------------------------------------------------------
// Uptime, /setup view, /botstatus
// ---------------------------------------------------------------------------------------
function uptimeEmbed(stats, ctx) {
  const dot = (pct) => (pct >= 99.9 ? '✓' : pct >= 99 ? '!' : '✕');
  const o = stats.overall;
  const lines = stats.services
    .slice(0, 15)
    .map(
      (s) =>
        `${dot(s.pct)} **${trim(s.name, 40)}** — ${s.pct.toFixed(2)}% • ${s.count} incident${s.count === 1 ? '' : 's'}` +
        (s.downMs ? ` • ${duration(s.downMs)}` : ''),
    );
  return new EmbedBuilder()
    .setTitle(`Roblox uptime, last ${stats.days} day${stats.days === 1 ? '' : 's'}`)
    .setURL(ctx.statusPageUrl)
    .setColor(COLOR_GREY)
    .setDescription(
      trim(
        `**${o.count}** incident${o.count === 1 ? '' : 's'} • **${duration(o.downMs)}** of incident time • ` +
          `**${o.pct.toFixed(2)}%** incident-free\n\n${lines.join('\n') || 'No services recorded yet.'}`,
        4000,
      ),
    )
    .setFooter({ text: 'Based on incidents Roblox reported, not measured by this bot.' })
    .setTimestamp(new Date());
}

function logViewEmbed(rows, roles) {
  const lines = rows.map(({ label, description, entry }) =>
    entry
      ? `✓ **${label}** → <#${entry.channelId}>\n${description}`
      : `· **${label}** — not set\n${description}`,
  );
  const embed = new EmbedBuilder()
    .setTitle('Log channels')
    .setColor(COLOR_GREY)
    .setDescription(trim(lines.join('\n\n'), 4000));
  const roleLines = roles.map((r) => `**${r.service === '*' ? 'All alerts' : r.service}** → <@&${r.roleId}>`);
  embed.addFields(field('Alert roles', roleLines.join('\n') || 'None. Set one with `/setup role`.'));
  return embed;
}

function botStatusEmbed(info) {
  const yes = '✓';
  const no = '✕';
  const embed = new EmbedBuilder()
    .setTitle('Bot status')
    .setColor(info.problems ? meta('degraded').color : meta('operational').color)
    .addFields(
      field('Uptime', info.uptime, true),
      field('Discord ping', info.ping, true),
      field('Memory', info.memory, true),
      field('Database', info.database),
      field('Roblox status API', info.statusApi),
      field(
        'Direct checks',
        info.health.length
          ? info.health.map((h) => `${h.ok === null ? '…' : h.ok ? yes : no} **${h.name}** — ${h.text}`).join('\n')
          : 'Disabled',
      ),
      field(
        'News feeds',
        info.news.length
          ? info.news.map((n) => `${n.ok === null ? '…' : n.ok ? yes : no} **${n.name}** — ${n.text}`).join('\n')
          : 'Disabled',
      ),
      field('Logs set up', info.logs),
    );
  if (info.mirror) embed.addFields(field('Message mirror', info.mirror));
  if (info.memberCount) embed.addFields(field('Member count status', info.memberCount));
  if (info.economy) embed.addFields(field('Economy tracker', info.economy));
  if (info.version) embed.setFooter({ text: `Version ${info.version} • Node ${process.version}` });
  return embed.setTimestamp(new Date());
}

// ---------------------------------------------------------------------------------------
// Roblox profile lookup
// ---------------------------------------------------------------------------------------
function profilePanelEmbed() {
  return new EmbedBuilder()
    .setTitle('Roblox profile lookup')
    .setColor(0x00a2ff)
    .setDescription(
      'Look up any public Roblox account.\n\nPress **Search Roblox User**, type a username, and the result is shown only to you.',
    );
}

// ---------------------------------------------------------------------------------------
// Economy tracker
// ---------------------------------------------------------------------------------------
const robux = (n) => (n == null ? 'Not set' : n.toLocaleString('en-US'));
const signed = (p) => `${p >= 0 ? '+' : ''}${p.toFixed(1)}%`;

function marketEmbed({ movers, newItems, max = 10, tracked }) {
  const embed = new EmbedBuilder().setTitle('Limited market moves').setColor(0x00a2ff).setTimestamp(new Date());
  const line = (m) => `${m.pct >= 0 ? '+' : '-'} [${trim(m.name, 45)}](https://www.rolimons.com/item/${m.id}): ${m.metric} ${robux(m.from)} to ${robux(m.to)} (${signed(m.pct)})`;
  const gains = movers.filter((m) => m.pct > 0).slice(0, max);
  const drops = movers.filter((m) => m.pct < 0).slice(0, max);
  if (gains.length) embed.addFields(field('Rising', trim(gains.map(line).join('\n'), 1000)));
  if (drops.length) embed.addFields(field('Falling', trim(drops.map(line).join('\n'), 1000)));
  if (newItems.length) {
    const rows = newItems.slice(0, max).map((i) => `[${trim(i.name, 45)}](https://www.rolimons.com/item/${i.id})${i.rap ? ` (RAP ${robux(i.rap)})` : ''}`);
    embed.addFields(field(`New limiteds (${newItems.length})`, trim(rows.join('\n'), 1000)));
  }
  const extra = Math.max(0, movers.length - gains.length - drops.length);
  const parts = ['Data: Rolimons'];
  if (tracked) parts.push(`${tracked.toLocaleString('en-US')} items tracked`);
  if (extra) parts.push(`${extra} more moves not shown`);
  return embed.setFooter({ text: parts.join(' · ') });
}

function itemEmbed({ item, resale, history, imageUrl }) {
  const { sparkline } = require('./lib/economy');
  const embed = new EmbedBuilder()
    .setTitle(trim(item.acronym ? `${item.name} (${item.acronym})` : item.name, 256))
    .setURL(`https://www.rolimons.com/item/${item.id}`)
    .setColor(0x00a2ff);
  if (imageUrl) embed.setThumbnail(imageUrl);
  const rap = resale && Number.isFinite(resale.recentAveragePrice) ? resale.recentAveragePrice : item.rap;
  embed.addFields(
    field('RAP', rap == null ? 'Unavailable' : robux(rap), true),
    field('Value', robux(item.value), true),
    field('Item ID', `\`${item.id}\``, true),
  );
  if (resale) {
    const known = (v) => (Number.isFinite(v) ? v.toLocaleString('en-US') : 'Unavailable');
    embed.addFields(
      field('Original price', Number.isFinite(resale.originalPrice) ? robux(resale.originalPrice) : 'Unavailable', true),
      field('Sales', known(resale.sales), true),
      field('Stock', known(resale.assetStock), true),
    );
  }
  if (history && history.prices.length >= 2) {
    const first = history.prices[0];
    const last = history.prices[history.prices.length - 1];
    embed.addFields(
      field(`Price, last ${history.prices.length} days`, `\`${sparkline(history.prices)}\`\n${robux(first)} to ${robux(last)}${history.change == null ? '' : ` (${signed(history.change)})`}`),
    );
  }
  return embed.setFooter({ text: `Roblox${resale ? '' : ' (sales data unavailable)'} · Rolimons` });
}

module.exports = {
  marketEmbed,
  itemEmbed,
  profilePanelEmbed,
  healthEmbed,
  newsEmbed,
  botNoticeEmbed,
  uptimeEmbed,
  logViewEmbed,
  botStatusEmbed,
  successEmbed,
  boardEmbed,
  incidentOpenEmbed,
  incidentUpdateEmbed,
  incidentResolvedEmbed,
  serviceChangeEmbed,
  historyEmbed,
  errorEmbed,
};
