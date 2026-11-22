'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { ReactionRoles, EMOJIS } = require('../src/services/reactionRoles');
const { Notifier } = require('../src/services/notifier');
const { JsonStore } = require('../src/services/store');

const quiet = { log() {}, warn() {}, error() {} };
const tmp = () => new JsonStore(fs.mkdtempSync(path.join(os.tmpdir(), 'rr-')), 50);

function world() {
  const dms = [];
  const roles = new Set();
  const user = (id, { closed = false } = {}) => ({
    id, bot: false,
    send: async (p) => { if (closed) throw new Error('Cannot send messages to this user'); dms.push({ id, p }); },
  });
  const client = { users: { fetch: async (id) => user(id) } };
  const reaction = (emoji, messageId = 'M') => ({
    partial: false, emoji: { name: emoji },
    message: { id: messageId, guild: { members: { fetch: async () => ({ roles: { add: async (r) => roles.add(r), remove: async (r) => roles.delete(r) } }) } } },
  });
  return { dms, roles, user, client, reaction };
}

test('reacting subscribes, gives the role and DMs a confirmation; removing undoes it', async () => {
  const w = world();
  const rr = new ReactionRoles({ store: tmp(), client: w.client, log: quiet });
  await rr.setRole('game_updates', 'roleG');
  await rr.setMessage('c', 'M');
  const u = w.user('u1');
  await rr.handleAdd(w.reaction(EMOJIS.game_updates), u);
  assert.deepEqual(rr.subscribers('game_updates'), ['u1']);
  assert.ok(w.roles.has('roleG'));
  assert.equal(w.dms.length, 1);
  assert.match(w.dms[0].p.content, /<@u1>/);

  await rr.handleRemove(w.reaction(EMOJIS.game_updates), u);
  assert.deepEqual(rr.subscribers('game_updates'), []);
  assert.equal(w.roles.has('roleG'), false);
});

test('other messages, other emojis and bots are ignored; no role set still subscribes', async () => {
  const w = world();
  const rr = new ReactionRoles({ store: tmp(), client: w.client, log: quiet });
  await rr.setMessage('c', 'M');
  await rr.handleAdd(w.reaction(EMOJIS.api_status, 'OTHER'), w.user('a'));
  await rr.handleAdd(w.reaction('👍'), w.user('b'));
  await rr.handleAdd(w.reaction(EMOJIS.api_status), { ...w.user('bot'), bot: true });
  assert.deepEqual(rr.subscribers('api_status'), []);
  await rr.handleAdd(w.reaction(EMOJIS.api_status), w.user('c'));
  assert.deepEqual(rr.subscribers('api_status'), ['c']);
});

test('a role failure still subscribes the user', async () => {
  const w = world();
  const rr = new ReactionRoles({ store: tmp(), client: w.client, log: quiet });
  await rr.setRole('website_status', 'r');
  await rr.setMessage('c', 'M');
  const r = w.reaction(EMOJIS.website_status);
  r.message.guild.members.fetch = async () => { throw new Error('Missing Permissions'); };
  await rr.handleAdd(r, w.user('u'));
  assert.deepEqual(rr.subscribers('website_status'), ['u']);
  assert.match(w.dms[0].p.embeds[0].description, /could not give you the role/);
});

test('dm() sends embeds to subscribers of that log only, skips files and closed DMs', async () => {
  const w = world();
  const rr = new ReactionRoles({ store: tmp(), client: w.client, log: quiet });
  await rr.subscribe('game_updates', 'u1');
  await rr.subscribe('game_updates', 'u2');
  await rr.subscribe('api_status', 'u3');
  assert.equal(await rr.dm('game_updates', { embeds: [{ title: 'News' }] }), 2);
  assert.deepEqual(w.dms.map((d) => d.id), ['u1', 'u2']);
  assert.equal(await rr.dm('game_updates', { embeds: [], files: [{}] }), 0);
  w.client.users.fetch = async () => ({ send: async () => { throw new Error('closed'); } });
  assert.equal(await rr.dm('game_updates', { embeds: [{}] }), 0);
});

test('settings persist; Notifier DMs after a successful channel post only', async () => {
  const store = tmp();
  const w = world();
  const rr = new ReactionRoles({ store, client: w.client, log: quiet });
  await rr.setRole('bot_updates', 'rb');
  await rr.setMessage('c', 'M');
  await rr.subscribe('bot_updates', 'u1');
  const again = new ReactionRoles({ store, client: w.client, log: quiet });
  await again.load();
  assert.equal(again.roleFor('bot_updates'), 'rb');
  assert.deepEqual(again.subscribers('bot_updates'), ['u1']);

  let fail = true;
  const logChannels = { get: () => 'c', send: async () => { if (fail) { fail = false; throw new Error('x'); } } };
  const notifier = new Notifier({ logChannels, subscribers: again, log: quiet });
  await notifier.post('bot_updates', { embeds: [{ title: 'down' }] });
  assert.equal(w.dms.length, 0); // channel post failed -> no DM yet
  await notifier.flush();
  await new Promise((r) => setImmediate(r));
  assert.equal(w.dms.length, 1); // delivered once, after the retry worked
});
