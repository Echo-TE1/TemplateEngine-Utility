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
      if (level === 'operational') return `${m.emoji} **${term}** — Operational`;
      const where = [...new Set(matched.filter((s) => s.level === level).map((s) => s.group))];
      return `${m.emoji} **${term}** — ${m.label} (${where.join(', ')})`;
    }

    // Not a component on Roblox's status page: judged by the incidents that mention it.
    const mentioned = snap.incidents.some((i) => i.trackedTerms.includes(term));
    return mentioned
      ? `🟠 **${term}** — Incident reported`
      : `🟢 **${term}** — No incidents reported`;
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
    .setDescription(`${overall.emoji} **${headline}**${warning ? `\n\n⚠️ ${warning}` : ''}`)
    .addFields(field('Tracked Services', trackedLines(snap).join('\n')));

  if (snap.incidents.length) {
    const text = snap.incidents
      .slice(0, 5)
      .map((i) => {
        const latest = i.messages[i.messages.length - 1];
        return (
          `${meta(i.level).emoji} **${trim(i.name, 80)}**\n` +
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
      .map((m) => `🔧 **${trim(m.name, 80)}**\n${ts(m.plannedStart, 'f')} → ${ts(m.plannedEnd, 't')}`)
      .join('\n\n');
    embed.addFields(field('Maintenance', text));
  }

  // When anything is not fine, show exactly what is broken AND what is still working.
  const broken = snap.services.filter((s) => s.level !== 'operational');
  if (broken.length) {
    const shown = broken
      .slice(0, 15)
      .map((s) => `${meta(s.level).emoji} **${s.group} / ${s.name}** — ${meta(s.level).label}`);
    const more = broken.length > 15 ? `\n…and ${broken.length - 15} more` : '';
    embed.addFields(field(`Not Operational (${broken.length})`, shown.join('\n') + more));

    const working = snap.services.filter((s) => s.level === 'operational');
    const byGroup = new Map();
    for (const s of working) byGroup.set(s.group, [...(byGroup.get(s.group) || []), s.name]);
    const lines = [...byGroup].map(([group, names]) => `🟢 **${group}**: ${names.join(', ')}`);
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
    .setTitle(trim(`${m.emoji} Roblox Incident: ${incident.name}`, 256))
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
    .setTitle(trim(`🔄 Update: ${incident.name}`, 256))
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
    .setTitle(trim(`✅ Resolved: ${record.name}`, 256))
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
    .map((c) => `${meta(c.to).emoji} **${c.group} / ${c.name}** — ${meta(c.from).label} → ${meta(c.to).label}`);
  return new EmbedBuilder()
    .setTitle(allRecovered ? '🟢 Roblox services recovered' : '⚠️ Roblox service status changed')
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
    .setTitle('📜 Roblox Incident History')
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

module.exports = {
  successEmbed,
  boardEmbed,
  incidentOpenEmbed,
  incidentUpdateEmbed,
  incidentResolvedEmbed,
  serviceChangeEmbed,
  historyEmbed,
  errorEmbed,
};
