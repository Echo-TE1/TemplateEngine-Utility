'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { ServiceRoles } = require('../src/services/serviceRoles');
const { Notifier } = require('../src/services/notifier');
const { LogChannels } = require('../src/services/logChannels');
const { JsonStore } = require('../src/services/store');

const tmp = () => new JsonStore(fs.mkdtempSync(path.join(os.tmpdir(), 'svc-')), 50);
const quiet = { log() {}, warn() {}, error() {} };

test('JsonStore: documents persist and stay separate; log channels can be cleared', async () => {
  const store = tmp();
  assert.deepEqual(await store.getDoc('x'), {});
  await store.setDoc('x', { a: 1 });
  await store.setDoc('y', { b: 2 });
  assert.deepEqual(await store.getDoc('x'), { a: 1 });
  assert.deepEqual(await store.getDoc('y'), { b: 2 });

  await store.setLogChannel('bot_updates', { channelId: '1' });
  await store.setLogChannel('api_status', { channelId: '2' });
  await store.clearLogChannel('bot_updates');
  assert.deepEqual(await store.getLogChannels(), { api_status: { channelId: '2' } });
});

test('LogChannels.clear removes a log, saves it, and reports whether it existed', async () => {
  const store = tmp();
  const logs = new LogChannels({ client: {}, store });
  await logs.set('bot_updates', { channelId: '111' });
  assert.equal(await logs.clear('bot_updates'), true);
  assert.equal(logs.get('bot_updates'), null);
  assert.equal(await logs.clear('bot_updates'), false);
  const again = new LogChannels({ client: {}, store });
  await again.load();
  assert.equal(again.get('bot_updates'), null);
});

test('ServiceRoles: per-service roles, the "all" role, no duplicates, and persistence', async () => {
  const store = tmp();
  const roles = new ServiceRoles({ store });
  await roles.set('Data Store', 'r1');
  await roles.set('*', 'rAll');
  assert.deepEqual(roles.rolesFor(['DataStore']), ['r1', 'rAll']); // spacing and case do not matter
  assert.deepEqual(roles.rolesFor(['Avatar']), ['rAll']);
  await roles.set('Avatar', 'rAll'); // same role twice -> pinged once
  assert.deepEqual(roles.rolesFor(['Avatar']), ['rAll']);

  const reloaded = new ServiceRoles({ store });
  await reloaded.load();
  assert.equal(reloaded.get('Data Store'), 'r1');
  assert.equal(reloaded.list().length, 3);

  assert.equal(await reloaded.clear('Data Store'), true);
  assert.equal(await reloaded.clear('Data Store'), false);
  assert.deepEqual(reloaded.rolesFor(['Data Store']), ['rAll']);
});

test('ServiceRoles: ALERT_ROLE_ID is the fallback "all" role, and a saved one replaces it', async () => {
  const roles = new ServiceRoles({ store: tmp(), fallbackRoleId: 'env' });
  assert.deepEqual(roles.rolesFor(['Website']), ['env']);
  await roles.set('*', 'saved');
  assert.deepEqual(roles.rolesFor(['Website']), ['saved']);
});

test('ServiceRoles: a failed save leaves memory unchanged', async () => {
  const roles = new ServiceRoles({ store: { getDoc: async () => ({}), setDoc: async () => { throw new Error('disk full'); } } });
  await assert.rejects(() => roles.set('Website', 'r'), /disk full/);
  assert.equal(roles.get('Website'), null);
});

test('Notifier: skips logs that are not set up, posts without pings, retries then gives up', async () => {
  const sent = [];
  let failures = 0;
  const logChannels = {
    get: (type) => (type === 'not_set' ? null : 'chan'),
    send: async (type, payload) => {
      if (failures > 0) { failures -= 1; throw new Error('Discord is down'); }
      sent.push({ type, payload });
    },
  };
  const notifier = new Notifier({ logChannels, log: quiet });

  assert.equal(await notifier.post('not_set', { content: 'x' }), false);
  assert.equal(sent.length, 0);

  assert.equal(await notifier.post('bot_updates', { content: 'hi' }), true);
  assert.deepEqual(sent[0].payload.allowedMentions, { parse: [] });
  await notifier.post('bot_updates', { content: 'ping', allowedMentions: { roles: ['1'] } });
  assert.deepEqual(sent[1].payload.allowedMentions, { roles: ['1'] });

  failures = 1; // one failure: queued, then delivered by the retry
  assert.equal(await notifier.post('bot_updates', { content: 'retry me' }), false);
  await notifier.flush();
  assert.equal(sent.filter((s) => s.payload.content === 'retry me').length, 1);

  failures = 99; // always failing: tried 3 times in total, then dropped
  await notifier.post('bot_updates', { content: 'doomed' });
  await notifier.flush();
  await notifier.flush();
  await notifier.flush();
  assert.equal(failures, 99 - 3);
  assert.equal(notifier.queue.length, 0);
});
