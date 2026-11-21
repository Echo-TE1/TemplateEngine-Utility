'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { diff, emptyState } = require('../src/lib/diff');
const { rawStatus, rawIncident, snapshot } = require('./helpers');

const OPTS = { scope: 'tracked', baseline: false, resolveConfirm: 2 };
const types = (events) => events.map((e) => e.type);

// Runs several snapshots in a row, like the poll loop does, and returns every result.
function run(snaps, options = OPTS, state = emptyState()) {
  const results = [];
  for (const snap of snaps) {
    const result = diff(state, snap, options);
    state = result.next;
    results.push(result);
  }
  return results;
}

test('first run with baseline stays silent but remembers running incidents', () => {
  const snap = snapshot(rawStatus({ incidents: [rawIncident('a')] }));
  const [first, second] = run([snap, snap], { ...OPTS, baseline: true });
  assert.deepEqual(first.events, []);
  assert.equal(first.next.initialized, true);
  assert.equal(first.next.incidents.a.announced, true);
  assert.deepEqual(second.events, []);
});

test('first run without baseline announces running incidents', () => {
  const [first] = run([snapshot(rawStatus({ incidents: [rawIncident('a')] }))]);
  assert.deepEqual(types(first.events), ['incident_open']);
});

test('an incident is announced once, however many polls still show it', () => {
  const clear = snapshot(rawStatus());
  const active = snapshot(rawStatus({ incidents: [rawIncident('a')] }));
  const results = run([clear, active, active, active]);
  assert.deepEqual(results.map((r) => types(r.events)), [[], ['incident_open'], [], []]);
});

test('new updates are announced once each, in order', () => {
  const m1 = { details: 'Investigating', state: 100, datetime: '2026-11-21T10:00:00.000Z' };
  const m2 = { details: 'Identified the cause', state: 200, datetime: '2026-11-21T10:20:00.000Z' };
  const s1 = snapshot(rawStatus({ incidents: [rawIncident('a', { messages: [m1] })] }));
  const s2 = snapshot(rawStatus({ incidents: [rawIncident('a', { messages: [m1, m2] })] }));
  const results = run([snapshot(rawStatus()), s1, s2, s2]);

  assert.deepEqual(results.map((r) => types(r.events)), [[], ['incident_open'], ['incident_update'], []]);
  assert.deepEqual(results[2].events[0].updates.map((u) => u.details), ['Identified the cause']);
});

test('resolution needs consecutive polls, then is announced once and saved to history', () => {
  const clear = snapshot(rawStatus(), '2026-11-21T11:00:00.000Z');
  const active = snapshot(rawStatus({ incidents: [rawIncident('a')] }));
  const results = run([snapshot(rawStatus()), active, clear, clear, clear]);

  assert.deepEqual(types(results[2].events), []); // only missing once: could be a glitch
  assert.deepEqual(types(results[3].events), ['incident_resolved']);
  assert.deepEqual(types(results[4].events), []);

  const [record] = results[3].history;
  assert.equal(record.id, 'a');
  assert.equal(record.durationMs, 60 * 60 * 1000);
  assert.equal(Object.keys(results[3].next.incidents).length, 0);
});

test('a one-poll glitch does not resolve and re-open an incident', () => {
  const clear = snapshot(rawStatus());
  const active = snapshot(rawStatus({ incidents: [rawIncident('a')] }));
  const results = run([clear, active, clear, active, active]);
  assert.deepEqual(results.map((r) => types(r.events)), [[], ['incident_open'], [], [], []]);
});

test('scope "tracked" ignores incidents about services you do not track', () => {
  const forum = rawIncident('f', { name: 'Forum slow', services: ['Forum'] });
  const snap = snapshot(rawStatus({ incidents: [forum] }));
  const [tracked] = run([snap], OPTS, { ...emptyState(), initialized: true });
  assert.deepEqual(tracked.events, []);

  const [all] = run([snap], { ...OPTS, scope: 'all' }, { ...emptyState(), initialized: true });
  assert.deepEqual(types(all.events), ['incident_open']);
});

test('services going down and recovering produce one change event each', () => {
  const ok = snapshot(rawStatus());
  const down = snapshot(rawStatus({ codes: { 'Creator/Data Store': 500 } }));
  const results = run([ok, down, down, ok]);

  assert.deepEqual(results.map((r) => types(r.events)), [[], ['service_change'], [], ['service_change']]);
  assert.equal(results[1].events[0].changes[0].to, 'outage');
  assert.equal(results[3].events[0].changes[0].to, 'operational');
});

test('no separate service message when an incident start already covers that service', () => {
  const ok = snapshot(rawStatus());
  const both = snapshot(
    rawStatus({ codes: { 'Creator/Data Store': 500 }, incidents: [rawIncident('a')] }),
  );
  const [, second] = run([ok, both]);
  assert.deepEqual(types(second.events), ['incident_open']);
});

test('untracked services do not trigger service messages in tracked scope', () => {
  const ok = snapshot(rawStatus());
  const down = snapshot(rawStatus({ codes: { 'Creator/Forum': 500 } }));
  const [, second] = run([ok, down]);
  assert.deepEqual(second.events, []);
});

test('an incident that gains a tracked service later gets announced then', () => {
  const forum = snapshot(
    rawStatus({ incidents: [rawIncident('a', { name: 'Platform issue', services: ['Forum'] })] }),
  );
  const widened = snapshot(
    rawStatus({ incidents: [rawIncident('a', { name: 'Platform issue', services: ['Forum', 'Avatar'] })] }),
  );
  const results = run([snapshot(rawStatus()), forum, widened, widened]);
  assert.deepEqual(results.map((r) => types(r.events)), [[], [], ['incident_open'], []]);
});

test('every resolved incident goes to history, even ones that were never announced', () => {
  const forum = snapshot(
    rawStatus({ incidents: [rawIncident('f', { name: 'Forum slow', services: ['Forum'] })] }),
  );
  const clear = snapshot(rawStatus());
  const results = run([snapshot(rawStatus()), forum, clear, clear]);
  assert.deepEqual(results[3].events, []);
  assert.equal(results[3].history.length, 1);
});
