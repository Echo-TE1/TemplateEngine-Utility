'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { normalize } = require('../src/lib/normalize');
const { annotate } = require('../src/lib/match');
const { rawStatus, rawIncident, snapshot } = require('./helpers');

test('flattens groups into services and keeps same-named services apart', () => {
  const snap = normalize(rawStatus());
  const websites = snap.services.filter((s) => s.name === 'Website');
  assert.equal(websites.length, 3);
  assert.equal(new Set(websites.map((s) => s.key)).size, 3);
  assert.equal(snap.overall.level, 'operational');
});

test('maps status codes to levels and raises the overall level', () => {
  const snap = normalize(rawStatus({ codes: { 'Creator/Data Store': 400 } }));
  assert.equal(snap.services.find((s) => s.key === 'Creator/Data Store').level, 'partial');
  assert.equal(snap.overall.level, 'partial');
});

test('normalizes incidents: strips HTML, sorts updates, colours by affected service', () => {
  const raw = rawStatus({
    codes: { 'Creator/Data Store': 500 },
    incidents: [
      rawIncident('abc', {
        messages: [
          { details: '<p>Fixed &amp; monitoring</p>', state: 300, datetime: '2026-11-21T10:30:00.000Z' },
          { details: 'Investigating', state: 100, datetime: '2026-11-21T10:00:00.000Z' },
        ],
      }),
    ],
  });
  const [incident] = normalize(raw).incidents;
  assert.equal(incident.level, 'outage');
  assert.deepEqual(incident.messages.map((m) => m.details), ['Investigating', 'Fixed & monitoring']);
});

test('an open incident is never shown as operational', () => {
  const [incident] = normalize(rawStatus({ incidents: [rawIncident('abc')] })).incidents;
  assert.equal(incident.level, 'degraded');
});

test('tracked matching ignores case and spaces; Authentication is matched by incident text', () => {
  const raw = rawStatus({
    incidents: [
      rawIncident('forum', { name: 'Forum slow', services: ['Forum'] }),
      rawIncident('login', { name: 'Login failures', services: ['Website'], groups: ['User'] }),
      rawIncident('auth-only', {
        name: 'Users cannot sign in',
        services: ['Studio'],
        messages: [{ details: 'Sign in is failing', state: 100, datetime: '2026-11-21T10:00:00.000Z' }],
      }),
    ],
  });
  const snap = annotate(normalize(raw), ['website', 'DataStore', 'Authentication']);
  assert.equal(snap.services.find((s) => s.key === 'Creator/Data Store').tracked, true);
  assert.equal(snap.services.find((s) => s.key === 'Creator/Forum').tracked, false);
  assert.deepEqual(snap.tracking.virtual, ['Authentication']);

  const byId = Object.fromEntries(snap.incidents.map((i) => [i.id, i]));
  assert.equal(byId.forum.tracked, false);
  assert.deepEqual(byId.login.trackedTerms.sort(), ['Authentication', 'website']);
  assert.deepEqual(byId['auth-only'].trackedTerms, ['Authentication']);
});

test('rejects responses that are not Status.io shaped', () => {
  assert.throws(() => normalize({}), /Unexpected response/);
  assert.throws(() => normalize(null), /Unexpected response/);
});

test('tolerates missing optional fields', () => {
  const raw = { result: { status: [{ name: 'Creator', containers: [{ name: 'Studio' }] }] } };
  const snap = normalize(raw);
  assert.equal(snap.services.length, 1);
  assert.equal(snap.incidents.length, 0);
  assert.equal(snapshot(rawStatus()).tracking.terms.length, 6);
});
