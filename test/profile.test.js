'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { RobloxProfiles, ProfileError } = require('../src/services/robloxProfile');
const { ProfilePanel } = require('../src/services/profilePanel');
const { JsonStore } = require('../src/services/store');

const quiet = { log() {}, warn() {}, error() {} };

// A fake Roblox: route table of "METHOD url-substring" -> [status, body, headers?]
function fakeFetch(routes, calls = []) {
  return async (url, options = {}) => {
    const method = options.method || 'GET';
    calls.push({ method, url, options });
    for (const [key, value] of Object.entries(routes).sort((a, b) => b[0].length - a[0].length)) { // most specific route wins
      const [m, part] = key.split(' ');
      if (m === method && url.includes(part)) {
        const reply = typeof value === 'function' ? value(options, calls) : value;
        const [status, body, headers = {}] = reply;
        if (body === 'THROW') throw new Error('network down');
        return { status, headers: new Map(Object.entries(headers)), json: async () => body };
      }
    }
    return { status: 404, headers: new Map(), json: async () => ({}) };
  };
}

const FULL = {
  'POST usernames/users': [200, { data: [{ id: 7, name: 'Builder' }] }],
  'GET /v1/users/7': [200, { id: 7, name: 'Builder', displayName: 'The Builder', hasVerifiedBadge: true, description: 'hi', created: '2016-01-02T00:00:00Z', isBanned: false }],
  'GET thumbnails': [200, { data: [{ state: 'Completed', imageUrl: 'https://tr.rbxcdn.com/a.png' }] }],
  'GET followers/count': [200, { count: 1500 }],
  'GET followings/count': [200, { count: 12 }],
  'GET friends/count': [200, { count: 30 }],
  'GET roblox-badges': [200, [{ id: 1, name: 'Administrator', imageUrl: 'https://img/1.png' }]],
  'GET rolimons': [200, { success: true, rap: 1000, value: 2500, rank: 42, privacy_enabled: false, last_online: 1700000000, stats_updated: 1700000100 }],
};

test('validUsername accepts real names and rejects bad ones', () => {
  assert.equal(RobloxProfiles.validUsername(' Builder_1 '), 'Builder_1');
  for (const bad of ['ab', 'a'.repeat(21), '_abc', 'abc_', 'a_b_c', 'has space', 'bad!', '', null]) {
    assert.equal(RobloxProfiles.validUsername(bad), null, String(bad));
  }
});

test('a full lookup collects everything and never invents numbers', async () => {
  const p = await new RobloxProfiles({ fetchImpl: fakeFetch(FULL), log: quiet }).lookup('Builder');
  assert.equal(p.id, '7');
  assert.equal(p.displayName, 'The Builder');
  assert.equal(p.verified, true);
  assert.equal(p.followers, 1500);
  assert.equal(p.following, 12);
  assert.equal(p.avatarUrl, 'https://tr.rbxcdn.com/a.png');
  assert.deepEqual(p.badges, [{ name: 'Administrator', imageUrl: 'https://img/1.png' }]);
  assert.equal(p.rolimons.rap, 1000);
  assert.equal(p.rolimons.value, 2500);
  assert.equal(p.rolimons.lastOnline, new Date(1700000000 * 1000).toISOString());
  assert.equal(p.presence, null); // no login configured: not available, not guessed
  assert.equal(p.url, 'https://www.roblox.com/users/7/profile');
});

test('missing sections stay null while the card still works', async () => {
  const routes = { ...FULL, 'GET thumbnails': [500, {}], 'GET followers/count': [200, {}], 'GET rolimons': [200, { success: false }], 'GET roblox-badges': [429, {}], 'GET followings/count': [200, 'THROW'] };
  const p = await new RobloxProfiles({ fetchImpl: fakeFetch(routes), log: quiet }).lookup('Builder');
  assert.equal(p.avatarUrl, null);
  assert.equal(p.followers, null);
  assert.equal(p.following, null);
  assert.equal(p.rolimons, null);
  assert.equal(p.badges, null);
  assert.equal(p.rateLimited, true);
  assert.equal(p.friends, 30);
});

test('Rolimons private inventories hide RAP and value', async () => {
  const routes = { ...FULL, 'GET rolimons': [200, { success: true, privacy_enabled: true, rap: 5, value: 5, rank: 1 }] };
  const p = await new RobloxProfiles({ fetchImpl: fakeFetch(routes), log: quiet }).lookup('Builder');
  assert.equal(p.rolimons.private, true);
  assert.equal(p.rolimons.rap, null);
  assert.equal(p.rolimons.value, null);
});

test('errors: invalid name, unknown account, rate limit, outage, network failure', async () => {
  const mk = (routes) => new RobloxProfiles({ fetchImpl: fakeFetch(routes), log: quiet });
  const kind = async (p) => p.catch((e) => (e instanceof ProfileError ? e.kind : `other:${e.message}`));

  assert.equal(await kind(mk(FULL).lookup('x')), 'invalid');
  assert.equal(await kind(mk({ 'POST usernames/users': [200, { data: [] }] }).lookup('Nobody')), 'notfound');
  assert.equal(await kind(mk({ 'POST usernames/users': [429, {}] }).lookup('Builder')), 'ratelimited');
  assert.equal(await kind(mk({ 'POST usernames/users': [503, {}] }).lookup('Builder')), 'error');
  assert.equal(await kind(mk({ 'POST usernames/users': [200, 'THROW'] }).lookup('Builder')), 'error');
  assert.equal(await kind(mk({ ...FULL, 'GET /v1/users/7': [404, {}] }).lookup('Builder')), 'notfound');
  assert.equal(await kind(mk({ ...FULL, 'GET /v1/users/7': [429, {}] }).lookup('Builder')), 'ratelimited');
});

test('results are cached briefly; Refresh forces a new fetch after a few seconds', async () => {
  const calls = [];
  let t = 1_000_000;
  const p = new RobloxProfiles({ fetchImpl: fakeFetch(FULL, calls), log: quiet, now: () => t });
  await p.lookup('Builder');
  const first = calls.length;
  await p.lookup('builder'); // name and profile cached
  assert.equal(calls.length, first);
  t += 6_000;
  await p.byId('7', { force: true });
  assert.ok(calls.length > first);
});

test('presence: needs the cookie, retries once with the CSRF token, and never leaks the cookie', async () => {
  const calls = [];
  const routes = {
    ...FULL,
    'POST presence': (options) =>
      options.headers['x-csrf-token']
        ? [200, { userPresences: [{ userPresenceType: 2, lastOnline: '2026-11-22T05:00:00Z' }] }]
        : [403, {}, { 'x-csrf-token': 'tok' }],
  };
  const p = await new RobloxProfiles({ cookie: 'SECRET', fetchImpl: fakeFetch(routes, calls), log: quiet }).lookup('Builder');
  assert.deepEqual(p.presence, { status: 'In game', online: true, lastOnline: '2026-11-22T05:00:00Z' });
  assert.equal(JSON.stringify(p).includes('SECRET'), false);

  const without = await new RobloxProfiles({ fetchImpl: fakeFetch(FULL), log: quiet }).lookup('Builder');
  assert.equal(without.presence, null);
});

test('ProfilePanel: posts, replaces the old panel, restores after a redeploy, re-posts if deleted', async () => {
  const store = new JsonStore(fs.mkdtempSync(path.join(os.tmpdir(), 'pp-')), 50);
  const messages = new Map();
  let n = 0;
  const channel = (id) => ({
    id,
    isTextBased: () => true,
    send: async (payload) => { const m = { id: `m${++n}`, edits: 0, edit: async () => { m.edits += 1; } }; messages.set(m.id, m); m.payload = payload; return m; },
    messages: {
      fetch: async (mid) => { if (!messages.has(mid)) throw new Error('Unknown Message'); return messages.get(mid); },
      delete: async (mid) => { messages.delete(mid); },
    },
  });
  const chans = { c1: channel('c1'), c2: channel('c2') };
  const client = { channels: { cache: new Map(Object.entries(chans)), fetch: async (id) => chans[id] } };
  const mk = () => new ProfilePanel({ store, client, buildPayload: () => ({ embeds: [] }), log: quiet });

  const panel = mk();
  assert.equal(await panel.ensure(), 'none');
  await panel.publish(chans.c1);
  await panel.publish(chans.c2); // moving it removes the old one
  assert.equal(messages.size, 1);

  const again = mk();
  await again.load(); // after a redeploy
  assert.equal(await again.ensure(), 'ok');
  messages.clear(); // someone deletes it
  assert.equal(await again.ensure(), 'reposted');
  assert.equal(messages.size, 1);
});
