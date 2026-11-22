'use strict';

const { norm } = require('./match');

const DAY_MS = 86_400_000;

// Total length of the union of intervals (overlapping incidents are not counted twice).
function unionMs(intervals) {
  const sorted = [...intervals].sort((a, b) => a[0] - b[0]);
  let total = 0;
  let [curStart, curEnd] = sorted[0] || [0, 0];
  for (let i = 1; i < sorted.length; i += 1) {
    const [s, e] = sorted[i];
    if (s <= curEnd) {
      curEnd = Math.max(curEnd, e);
    } else {
      total += curEnd - curStart;
      [curStart, curEnd] = [s, e];
    }
  }
  return sorted.length ? total + (curEnd - curStart) : 0;
}

/**
 * Incident-based uptime over the last `days` days, from finished incidents (history) plus
 * incidents that are still open. This is what Roblox reported, not something we measured.
 *
 * history: [{ openedAt, resolvedAt, services[] }]   open: [{ openedAt, services[] }]
 * include: service names to list even if they had no incidents (shown at 100%)
 */
function uptimeStats(history, { now = Date.now(), days = 30, open = [], include = [] } = {}) {
  const windowMs = days * DAY_MS;
  const start = now - windowMs;

  const byService = new Map(); // key -> { name, intervals[], count }
  const overall = [];
  let incidentCount = 0;

  const slot = (name) => {
    const key = norm(name);
    if (!byService.has(key)) byService.set(key, { name, intervals: [], count: 0 });
    return byService.get(key);
  };
  for (const name of include) slot(name);

  const records = [
    ...history.map((r) => ({ ...r, end: Date.parse(r.resolvedAt) })),
    ...open.map((r) => ({ ...r, end: now })),
  ];

  for (const record of records) {
    const opened = Date.parse(record.openedAt);
    if (!Number.isFinite(opened) || !Number.isFinite(record.end) || record.end < start) continue;
    const interval = [Math.max(opened, start), Math.min(Math.max(record.end, opened), now)];
    if (interval[1] < interval[0]) continue;

    incidentCount += 1;
    overall.push(interval);
    const names = record.services && record.services.length ? record.services : ['Unspecified'];
    for (const name of new Set(names)) {
      const entry = slot(name);
      entry.count += 1;
      entry.intervals.push(interval);
    }
  }

  const row = (name, intervals, count) => {
    const downMs = unionMs(intervals);
    return { name, count, downMs, pct: Math.max(0, (1 - downMs / windowMs) * 100) };
  };

  return {
    days,
    overall: row('All incidents', overall, incidentCount),
    services: [...byService.values()]
      .map((s) => row(s.name, s.intervals, s.count))
      .sort((a, b) => b.downMs - a.downMs || a.name.localeCompare(b.name)),
  };
}

module.exports = { uptimeStats, unionMs };
