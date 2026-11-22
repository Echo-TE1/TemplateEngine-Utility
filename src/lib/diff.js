'use strict';

const { createHash } = require('node:crypto');
const { trim } = require('./format');
const { norm } = require('./match');

const MAX_UPDATES_PER_EVENT = 3;
const MAX_STORED_UPDATES = 20;

function emptyState() {
  return {
    initialized: false, // false until the first successful poll
    services: {}, // key -> { level }
    incidents: {}, // id -> record of every incident that is currently open
    statusMessage: null, // { channelId, messageId, hash, editedAt }
  };
}

/** Identifies one incident update, so the same update is never announced twice. */
function updateKey(message) {
  if (message.datetime) return message.datetime;
  return createHash('sha1').update(String(message.details)).digest('hex').slice(0, 12);
}

/**
 * Compares the saved state with a fresh snapshot.
 * Pure function: returns the next state, the events to announce, and finished incidents for the history.
 *
 * options:
 *   scope           'tracked' | 'all'  which incidents/services may be announced
 *   baseline        true  = record what is already happening without announcing it (first run)
 *   resolveConfirm  how many polls in a row an incident must be missing before it counts as resolved
 */
function diff(prev, snapshot, options = {}) {
  const { scope = 'tracked', baseline = false, resolveConfirm = 2 } = options;
  const next = structuredClone(prev);
  const events = [];
  const history = [];
  const inScope = (item) => scope === 'all' || item.tracked;

  // ---- Incidents: start, update ------------------------------------------------------
  const activeIds = new Set();
  const opened = [];

  for (const incident of snapshot.incidents) {
    activeIds.add(incident.id);
    const keys = incident.messages.map(updateKey);

    let record = next.incidents[incident.id];
    if (!record) {
      record = { id: incident.id, announced: false, seen: [], missing: 0, messageId: null };
      next.incidents[incident.id] = record;
    }

    // Keep the record fresh: it is all we know about the incident once Roblox removes it.
    Object.assign(record, {
      name: incident.name,
      openedAt: incident.openedAt,
      services: incident.services,
      groups: incident.groups,
      level: incident.level,
      tracked: incident.tracked,
      trackedTerms: incident.trackedTerms || [], // lets the "resolved" message ping the right roles
      missing: 0,
      updates: incident.messages
        .slice(-MAX_STORED_UPDATES)
        .map((m) => ({ datetime: m.datetime, state: m.state, details: trim(m.details, 500) })),
    });

    if (!record.announced && inScope(incident)) {
      record.announced = true;
      if (!baseline) {
        events.push({ type: 'incident_open', incident });
        opened.push(incident);
      }
      record.seen = keys; // the "started" message already shows the latest update
    } else if (record.announced) {
      const fresh = incident.messages.filter((m) => !record.seen.includes(updateKey(m)));
      if (fresh.length) {
        events.push({
          type: 'incident_update',
          incident,
          updates: fresh.slice(-MAX_UPDATES_PER_EVENT),
        });
      }
      record.seen = keys;
    } else {
      record.seen = keys; // out of scope: remember it, say nothing
    }
  }

  // ---- Incidents: resolved -----------------------------------------------------------
  for (const [id, record] of Object.entries(next.incidents)) {
    if (activeIds.has(id)) continue;

    // Wait a few polls so one empty/glitchy API answer cannot "resolve" and then "re-open" an incident.
    record.missing = (record.missing || 0) + 1;
    if (record.missing < resolveConfirm) continue;

    const resolvedAt = snapshot.fetchedAt;
    const started = Date.parse(record.openedAt);
    history.push({
      id: record.id,
      name: record.name,
      openedAt: record.openedAt,
      resolvedAt,
      durationMs: Number.isFinite(started) ? Date.parse(resolvedAt) - started : null,
      services: record.services,
      groups: record.groups,
      level: record.level,
      updates: record.updates,
    });
    if (record.announced) {
      events.push({ type: 'incident_resolved', record: { ...record }, resolvedAt });
    }
    delete next.incidents[id];
  }

  // ---- Services: down / degraded / recovered -----------------------------------------
  // Services already covered by an "incident started" message in this same poll are skipped,
  // so one outage does not produce two notifications at once.
  const covered = new Set(opened.flatMap((i) => i.services.map(norm)));
  const changes = [];
  const seenKeys = new Set();

  for (const service of snapshot.services) {
    seenKeys.add(service.key);
    const before = prev.services[service.key];
    if (before && before.level !== service.level && inScope(service) && !covered.has(norm(service.name))) {
      changes.push({
        key: service.key,
        group: service.group,
        name: service.name,
        from: before.level,
        to: service.level,
      });
    }
    next.services[service.key] = { level: service.level };
  }
  for (const key of Object.keys(next.services)) {
    if (!seenKeys.has(key)) delete next.services[key];
  }
  if (changes.length && !baseline) events.push({ type: 'service_change', changes });

  next.initialized = true;
  return { next, events, history };
}

module.exports = { diff, emptyState, updateKey };
