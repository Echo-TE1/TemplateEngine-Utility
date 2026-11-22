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
  await rr.handleAdd(w.reaction('\u{1F44D}'), w.user('b'));
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

test('ensureRoles creates a role per log, reuses by name, recreates deleted ones, is idempotent', async () => {
  const store = tmp();
  const roles = new Map([['x1', { id: 'x1', name: 'API Status Alerts' }]]);
  let n = 0;
  const guild = {
    roles: {
      fetch: async () => roles,
      create: async ({ name }) => { const r = { id: `new${++n}`, name }; roles.set(r.id, r); return r; },
    },
  };
  const client = { channels: { cache: new Map(), fetch: async () => ({ guild }) } };
  const rr = new ReactionRoles({ store, client, log: quiet });
  const logChannels = { get: (t) => (t === 'bot_updates' ? 'chan' : null) };

  const created = await rr.ensureRoles(logChannels, {});
  assert.equal(created.length, 4); // API Status reused by name
  assert.equal(rr.roleFor('api_status'), 'x1');
  assert.equal(rr.roleFor('game_updates') !== null, true);
  assert.deepEqual(await rr.ensureRoles(logChannels, {}), []);

  roles.delete(rr.roleFor('game_updates')); // someone deleted it
  assert.deepEqual(await rr.ensureRoles(logChannels, {}), ['Game Updates Alerts']);
});

test('ensureRoles does nothing (and does not throw) when no server is known yet', async () => {
  const rr = new ReactionRoles({ store: tmp(), client: { channels: { cache: new Map(), fetch: async () => null } }, log: quiet });
  assert.deepEqual(await rr.ensureRoles({ get: () => null }, {}), []);
});

function fakeChannel(existing) {
  const sent = [];
  const message = {
    id: 'MSG', guild: { members: { fetch: async (id) => ({ roles: { add: async (r) => message.roleOps.push(['add', id, r]), remove: async (r) => message.roleOps.push(['remove', id, r]) } }) } },
    roleOps: [], edits: 0, reacted: [],
    reactions: { cache: new Map() },
    edit: async () => { message.edits += 1; },
    react: async (e) => { message.reacted.push(e); },
  };
  const channel = {
    id: 'CH', isTextBased: () => true,
    messages: { fetch: async (id) => { if (existing && id === 'MSG') return message; throw new Error('Unknown Message'); } },
    send: async (p) => { sent.push(p); return message; },
  };
  return { channel, message, sent };
}

test('ensureMessage: after a redeploy it refreshes the message, re-adds reactions and catches up', async () => {
  const { channel, message } = fakeChannel(true);
  const client = { channels: { cache: new Map([['CH', channel]]), fetch: async () => channel } };
  const rr = new ReactionRoles({ store: tmp(), client, log: quiet });
  await rr.setRole('game_updates', 'roleG');
  await rr.setMessage('CH', 'MSG');
  await rr.subscribe('game_updates', 'gone'); // removed their reaction while the bot was offline
  message.reactions.cache.set(EMOJIS.game_updates, { users: { fetch: async () => new Map([['n', { id: 'new', bot: false }], ['b', { id: 'bot', bot: true }]]) } });

  assert.equal(await rr.ensureMessage(), 'ok');
  assert.equal(message.edits, 1);
  assert.equal(message.reacted.length, 4); // the four emojis that were missing
  assert.deepEqual(rr.subscribers('game_updates'), ['new']);
  assert.deepEqual(message.roleOps, [['add', 'new', 'roleG'], ['remove', 'gone', 'roleG']]);
});

test('ensureMessage: a deleted message is posted again; nothing saved or unreachable channel is handled', async () => {
  const { channel, sent } = fakeChannel(false);
  const client = { channels: { cache: new Map([['CH', channel]]), fetch: async () => channel } };
  const rr = new ReactionRoles({ store: tmp(), client, log: quiet });
  assert.equal(await rr.ensureMessage(), 'none');
  await rr.setMessage('CH', 'OLD');
  assert.equal(await rr.ensureMessage(), 'reposted');
  assert.equal(sent.length, 1);
  assert.equal(rr.message.messageId, 'MSG');

  const lost = new ReactionRoles({ store: tmp(), client: { channels: { cache: new Map(), fetch: async () => { throw new Error('x'); } } }, log: quiet });
  await lost.setMessage('NOPE', 'M');
  assert.equal(await lost.ensureMessage(), 'missing-channel');
});
