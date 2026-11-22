'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseDuration, parseRoleIds, PERMISSION_OPTIONS, buildOverwrites } = require('../src/lib/channelOptions');
const { TempChannels } = require('../src/services/tempChannels');
const { JsonStore } = require('../src/services/store');

const quiet = { log() {}, warn() {}, error() {} };
const MIN = 60_000;

test('parseDuration understands the old units and rejects bad input', () => {
  assert.equal(parseDuration('1m'), MIN);
  assert.equal(parseDuration('2 hours'), 120 * MIN);
  assert.equal(parseDuration('1d'), 1440 * MIN);
  assert.equal(parseDuration('1w'), 7 * 1440 * MIN);
  assert.equal(parseDuration('1mo'), 30 * 1440 * MIN);
  assert.equal(parseDuration('1year'), 365 * 1440 * MIN);
  for (const bad of ['', 'abc', '0h', '1', '1x', '11year', '-1d', null]) assert.equal(parseDuration(bad), null, String(bad));
});

test('parseRoleIds reads mentions and ids, de-duplicates, reports unreadable parts', () => {
  const { ids, bad } = parseRoleIds('<@&111111111111111111>, 222222222222222222 <@&111111111111111111> nope');
  assert.deepEqual(ids, ['111111111111111111', '222222222222222222']);
  assert.deepEqual(bad, ['nope']);
});

test('19 permission options + 6 others fit in the 25-option limit', () => {
  assert.equal(Object.keys(PERMISSION_OPTIONS).length + 6, 25);
});

test('overwrites: roles make it private; no roles puts choices on @everyone; nothing chosen = none', () => {
  const roles = buildOverwrites({ everyoneId: 'E', botId: 'B', roleIds: ['R'], chosen: { send_messages: false } });
  assert.deepEqual(roles[0], { id: 'E', deny: ['ViewChannel'] });
  const r = roles.find((o) => o.id === 'R');
  assert.ok(r.deny.includes('SendMessages') && !r.allow.includes('SendMessages')); // your choice beats the default
  assert.ok(r.allow.includes('ViewChannel'));
  assert.equal(roles[roles.length - 1].id, 'B');

  const everyone = buildOverwrites({ everyoneId: 'E', botId: 'B', roleIds: [], chosen: { view_channel: true, speak: false } });
  assert.deepEqual(everyone[0], { id: 'E', allow: ['ViewChannel'], deny: ['Speak'] });

  assert.deepEqual(buildOverwrites({ everyoneId: 'E', botId: 'B', roleIds: [], chosen: {} }), []);
});

test('TempChannels deletes due channels only, tolerates missing ones, and survives a restart', async () => {
  const store = new JsonStore(fs.mkdtempSync(path.join(os.tmpdir(), 'tc-')), 50);
  const deleted = [];
  const mk = (id) => ({ id, guildId: 'g', name: id, delete: async () => deleted.push(id) });
  const chans = { a: mk('a'), b: mk('b') };
  const client = { channels: { cache: new Map(Object.entries(chans)), fetch: async (id) => { throw new Error('Unknown Channel'); } } };
  const t = new TempChannels({ client, store, log: quiet });
  await t.add(chans.a, 1000);
  await t.add(chans.b, 9000);
  await t.add({ id: 'gone', guildId: 'g', name: 'gone' }, 1000); // already deleted by someone

  const again = new TempChannels({ client, store, log: quiet });
  await again.load(); // after a redeploy
  assert.equal(await again.check(5000), 2);
  assert.deepEqual(deleted, ['a']);
  assert.deepEqual(Object.keys(again.items), ['b']);
  assert.equal(await again.check(5000), 0);
  assert.equal(await again.check(10_000), 1);
  assert.deepEqual(deleted, ['a', 'b']);
});
