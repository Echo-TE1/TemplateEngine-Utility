'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { LogChannels, LOG_TYPES, labelFor } = require('../src/services/logChannels');
const { JsonStore } = require('../src/services/store');

function setup() {
  const sent = [];
  const channel = { isTextBased: () => true, send: async (payload) => { sent.push(payload); return { id: 'm1' }; } };
  const client = { channels: { cache: new Map([['111111111111111111', channel]]), fetch: async () => null } };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'logch-'));
  return { sent, client, dir, store: new JsonStore(dir, 50) };
}

test('offers the five log categories', () => {
  assert.deepEqual(
    LOG_TYPES.map((t) => t.name),
    ['Platform Status', 'Website Status', 'API Status', 'Roblox Updates', 'Bot Updates'],
  );
  assert.equal(labelFor('api_status'), 'API Status');
  assert.equal(labelFor('nope'), null);
});

test('saved channels survive a restart (new instance, same store)', async () => {
  const { client, store, dir } = setup();
  const first = new LogChannels({ client, store });
  await first.load();
  assert.equal(first.get('bot_updates'), null);

  await first.set('bot_updates', { channelId: '111111111111111111', setBy: '1' });
  await first.set('api_status', { channelId: '222222222222222222', setBy: '1' });

  const second = new LogChannels({ client, store: new JsonStore(dir, 50) });
  await second.load();
  assert.equal(second.get('bot_updates'), '111111111111111111');
  assert.equal(second.get('api_status'), '222222222222222222');
  assert.equal(second.get('game_updates'), null);
});

test('setting a log again replaces the old channel', async () => {
  const { client, store } = setup();
  const logs = new LogChannels({ client, store });
  await logs.set('bot_updates', { channelId: '111111111111111111' });
  await logs.set('bot_updates', { channelId: '333333333333333333' });
  assert.equal(logs.get('bot_updates'), '333333333333333333');
});

test('send() posts to the saved channel, and does nothing if the log is not set up', async () => {
  const { client, store, sent } = setup();
  const logs = new LogChannels({ client, store });
  assert.equal(await logs.send('bot_updates', { content: 'hi' }), null);

  await logs.set('bot_updates', { channelId: '111111111111111111' });
  await logs.send('bot_updates', { content: 'hi' });
  assert.deepEqual(sent, [{ content: 'hi' }]);
});

test('memory is not updated when saving fails', async () => {
  const { client } = setup();
  const broken = { getLogChannels: async () => ({}), setLogChannel: async () => { throw new Error('disk full'); } };
  const logs = new LogChannels({ client, store: broken });
  await assert.rejects(() => logs.set('bot_updates', { channelId: '1' }), /disk full/);
  assert.equal(logs.get('bot_updates'), null);
});
