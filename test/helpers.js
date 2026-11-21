'use strict';

// Builds fake Status.io responses so the logic can be tested without the network.
const { normalize } = require('../src/lib/normalize');
const { annotate } = require('../src/lib/match');

const TRACKED = ['Website', 'Game Join', 'Avatar', 'Marketplace', 'Data Store', 'Authentication'];

const GROUPS = {
  User: ['Website', 'Avatar'],
  Player: ['Website', 'Games'],
  Creator: ['Website', 'Game Join', 'Avatar', 'Marketplace', 'Data Store', 'Studio', 'Forum'],
};

const LABELS = {
  100: 'Operational',
  200: 'Planned Maintenance',
  300: 'Degraded Performance',
  400: 'Partial Service Disruption',
  500: 'Service Disruption',
};

function rawStatus({ codes = {}, incidents = [], maintenance } = {}) {
  return {
    result: {
      status_overall: { updated: '2026-11-21T10:00:00.000Z', status: 'Operational', status_code: 100 },
      status: Object.entries(GROUPS).map(([group, names]) => ({
        id: group,
        name: group,
        status: 'Operational',
        status_code: 100,
        containers: names.map((name) => {
          const code = codes[`${group}/${name}`] ?? 100;
          return { id: name, name, status: LABELS[code], status_code: code };
        }),
      })),
      incidents,
      maintenance: maintenance || { active: [], upcoming: [] },
    },
  };
}

function rawIncident(id, opts = {}) {
  const {
    name = 'Data Store issues',
    services = ['Data Store'],
    groups = ['Creator'],
    open = '2026-11-21T10:00:00.000Z',
    messages = [{ details: 'We are investigating.', state: 100, datetime: open }],
  } = opts;
  return {
    _id: id,
    name,
    datetime_open: open,
    containers_affected: services.map((n) => ({ name: n, _id: n })),
    components_affected: groups.map((n) => ({ name: n, _id: n })),
    messages,
  };
}

function snapshot(raw, at = '2026-11-21T10:05:00.000Z', tracked = TRACKED) {
  return annotate(normalize(raw, at), tracked);
}

module.exports = { rawStatus, rawIncident, snapshot, TRACKED };
