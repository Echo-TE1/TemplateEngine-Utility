'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { uptimeStats, unionMs } = require('../src/lib/stats');

const NOW = Date.parse('2026-11-30T00:00:00.000Z');
const HOUR = 3_600_000;
const ago = (h) => new Date(NOW - h * HOUR).toISOString();
const inc = (startH, endH, services) => ({ openedAt: ago(startH), resolvedAt: ago(endH), services });

test('overlapping intervals are only counted once', () => {
  assert.equal(unionMs([[0, 10], [5, 15], [20, 25]]), 20);
  assert.equal(unionMs([]), 0);
  assert.equal(unionMs([[0, 10], [2, 3]]), 10);
});

test('counts incidents, downtime and percentage per service', () => {
  const stats = uptimeStats(
    [inc(48, 46, ['Data Store']), inc(30, 29, ['Data Store', 'Website']), inc(100, 99, ['Avatar'])],
    { now: NOW, days: 10 },
  );
  const ds = stats.services.find((s) => s.name === 'Data Store');
  assert.equal(ds.count, 2);
  assert.equal(ds.downMs, 3 * HOUR);
  assert.ok(Math.abs(ds.pct - (1 - 3 / 240) * 100) < 1e-9);
  assert.equal(stats.overall.count, 3);
  assert.equal(stats.overall.downMs, 4 * HOUR);
  assert.equal(stats.services[0].name, 'Data Store'); // worst first
});

test('two simultaneous incidents on one service count the time once', () => {
  const stats = uptimeStats([inc(10, 6, ['Website']), inc(8, 4, ['Website'])], { now: NOW, days: 7 });
  assert.equal(stats.services[0].downMs, 6 * HOUR);
  assert.equal(stats.services[0].count, 2);
});

test('incidents that ended before the window are ignored; ones crossing its start are clipped', () => {
  const stats = uptimeStats([inc(24 * 40, 24 * 39, ['Old']), inc(24 * 10 + 5, 24 * 10 - 5, ['Edge'])], { now: NOW, days: 10 });
  assert.equal(stats.services.find((s) => s.name === 'Old'), undefined);
  assert.equal(stats.services.find((s) => s.name === 'Edge').downMs, 5 * HOUR);
});

test('open incidents run until now', () => {
  const stats = uptimeStats([], { now: NOW, days: 1, open: [{ openedAt: ago(3), services: ['Avatar'] }] });
  assert.equal(stats.services[0].downMs, 3 * HOUR);
});

test('listed services with no incidents show 100%; service names merge ignoring case and spaces', () => {
  const stats = uptimeStats([inc(5, 4, ['data store'])], { now: NOW, days: 7, include: ['Data Store', 'Marketplace'] });
  assert.equal(stats.services.length, 2);
  assert.equal(stats.services.find((s) => s.name === 'Marketplace').pct, 100);
  assert.equal(stats.services.find((s) => s.name === 'Data Store').count, 1);
});

test('incidents without services are grouped as Unspecified; broken records are skipped', () => {
  const stats = uptimeStats([inc(5, 4, []), { openedAt: 'nope', resolvedAt: 'nope', services: ['X'] }], { now: NOW, days: 7 });
  assert.equal(stats.services.length, 1);
  assert.equal(stats.services[0].name, 'Unspecified');
});

test('no history at all gives a clean 100%', () => {
  const stats = uptimeStats([], { now: NOW, days: 30 });
  assert.equal(stats.overall.count, 0);
  assert.equal(stats.overall.pct, 100);
});
