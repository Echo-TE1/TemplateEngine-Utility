'use strict';

const { levelFromStatus, worst } = require('./levels');
const { norm, uniq } = require('./match');

function clean(html) {
  return String(html ?? '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .trim();
}

// Mongo does not like "." or "$" inside field names, and these keys are used as field names.
const safeKey = (s) => String(s).replace(/[.$]/g, '_');

// Status.io: a "component" is a group (User / Player / Creator), its "containers" are the services.
// The same service name (e.g. Website) can exist in several groups, so keys include the group.
function makeService(group, item) {
  const isGroupItself = group === item;
  return {
    key: safeKey(isGroupItself ? group.name : `${group.name}/${item.name}`),
    group: group.name,
    name: item.name,
    level: levelFromStatus(item.status_code, item.status),
    text: item.status || '',
  };
}

function makeEvent(raw, services) {
  const serviceNames = uniq((raw.containers_affected || []).map((x) => x && x.name));
  const groupNames = uniq((raw.components_affected || []).map((x) => x && x.name));

  const messages = (raw.messages || [])
    .map((m) => ({
      details: clean(m && m.details),
      state: m ? m.state : undefined,
      datetime: (m && m.datetime) || '',
    }))
    .sort((a, b) => String(a.datetime).localeCompare(String(b.datetime)));

  // Which current services does this event touch? Used to colour it by severity.
  let related = [];
  if (serviceNames.length) {
    const byName = services.filter((s) => serviceNames.some((n) => norm(n) === norm(s.name)));
    const byGroup = byName.filter((s) => groupNames.includes(s.group));
    related = byGroup.length ? byGroup : byName;
  } else if (groupNames.length) {
    related = services.filter((s) => groupNames.includes(s.group));
  }
  const level = worst(related.map((s) => s.level));

  return {
    id: String(raw._id || raw.id || raw.name || 'unknown'),
    name: raw.name || 'Untitled incident',
    openedAt: raw.datetime_open || (messages[0] && messages[0].datetime) || '',
    plannedStart: raw.datetime_planned_start || '',
    plannedEnd: raw.datetime_planned_end || '',
    services: serviceNames,
    groups: groupNames,
    messages,
    // An open incident is never shown as "operational", even if its services already recovered.
    level: level === 'operational' ? 'degraded' : level,
  };
}

/**
 * Turns the raw Status.io response into a small, stable shape:
 * { fetchedAt, overall, services[], incidents[], maintenance: { active[], upcoming[] } }
 */
function normalize(raw, fetchedAt = new Date().toISOString()) {
  const result = raw && raw.result;
  if (!result || !Array.isArray(result.status)) {
    throw new Error('Unexpected response from the Roblox status API.');
  }

  const services = [];
  for (const group of result.status) {
    const containers = Array.isArray(group.containers) ? group.containers : [];
    if (containers.length === 0) services.push(makeService(group, group));
    for (const container of containers) services.push(makeService(group, container));
  }

  const overallRaw = result.status_overall || {};
  const overallLevel = worst([
    levelFromStatus(overallRaw.status_code, overallRaw.status),
    ...services.map((s) => s.level),
  ]);

  const maintenanceRaw = result.maintenance || {};
  const maintenance = {
    active: (maintenanceRaw.active || []).map((m) => ({ ...makeEvent(m, services), level: 'maintenance' })),
    upcoming: (maintenanceRaw.upcoming || []).map((m) => ({ ...makeEvent(m, services), level: 'maintenance' })),
  };

  return {
    fetchedAt,
    overall: { level: overallLevel, text: overallRaw.status || '', updated: overallRaw.updated || '' },
    services,
    incidents: (result.incidents || []).map((i) => makeEvent(i, services)),
    maintenance,
  };
}

module.exports = { normalize, clean };
