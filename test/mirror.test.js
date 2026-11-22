'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { MessageMirror } = require('../src/services/mirror');
const { mirrorEmbed } = require('../src/lib/mirrorText');
const { JsonStore } = require('../src/services/store');

const quiet = { log() {}, warn() {}, error() {} };
const CH = '100';
const USER = '200';

function setup({ logSet = true } = {}) {
  const store = new JsonStore(fs.mkdtempSync(path.join(os.tmpdir(), 'mir-')), 50);
  const posted = new Map(); // copy id -> { embeds, deleted }
  let n = 0;
  const channel = {
    isTextBased: () => true,
    messages: {
      fetch: async (id) => {
        const m = posted.get(id);
        if (!m || m.deleted) throw new Error('Unknown Message');
        return m.api;
      },
    },
  };
  const logChannels = {
    client: { channels: { cache: new Map([['log', channel]]), fetch: async () => channel } },
    get: () => (logSet ? 'log' : null),
    send: async (_t, payload) => {
      if (!logSet) return null;
      const id = `copy${++n}`;
      const rec = { payload, deleted: false };
      rec.api = {
        id,
        edit: async (p) => { rec.payload = p; },
        delete: async () => { rec.deleted = true; },
      };
      posted.set(id, rec);
      return rec.api;
    },
  };
  const mirror = new MessageMirror({ store, logChannels, config: { mirrorChannelId: CH, mirrorUserId: USER }, log: quiet });
  return { mirror, posted, store, logChannels };
}

const msg = (over = {}) => ({
  id: 'm1', channelId: CH, content: 'hello', author: { id: USER, tag: 'owner' },
  attachments: new Map(), reactions: [], url: 'https://discord.com/x',
  react: async function (e) { this.reactions.push(e); }, partial: false, ...over,
});

test('mirrors only the tracked user in the tracked channel, and reacts with a check mark', async () => {
  const { mirror, posted } = setup();
  const a = msg();
  await mirror.onCreate(a);
  await mirror.onCreate(msg({ id: 'm2', author: { id: 'other' } }));
  await mirror.onCreate(msg({ id: 'm3', channelId: 'elsewhere' }));
  await mirror.idle();
  assert.equal(posted.size, 1);
  assert.deepEqual(a.reactions, ['✅']);
  assert.equal([...posted.values()][0].payload.embeds[0].description, 'hello');
  assert.deepEqual([...posted.values()][0].payload.allowedMentions, { parse: [] });
});

test('no check mark when Bot Updates is not set up', async () => {
  const { mirror, posted } = setup({ logSet: false });
  const a = msg();
  await mirror.onCreate(a);
  await mirror.idle();
  assert.equal(posted.size, 0);
  assert.deepEqual(a.reactions, []);
});

test('an edit changes the copy; an unchanged "edit" does nothing', async () => {
  const { mirror, posted } = setup();
  const a = msg();
  await mirror.onCreate(a);
  await mirror.onUpdate(a, msg({ content: 'hello (fixed)' }));
  const copy = [...posted.values()][0];
  assert.equal(copy.payload.embeds[0].description, 'hello (fixed)');
  assert.equal(copy.payload.embeds[0].footer.text, 'Edited');
  await mirror.onUpdate(msg(), msg());
  assert.equal(mirror.stats.edited, 1);
});

test('an edit to a message that was never mirrored posts it', async () => {
  const { mirror, posted } = setup();
  await mirror.onUpdate({ partial: true }, msg({ id: 'late', content: 'new text' }));
  assert.equal(posted.size, 1);
});

test('a partial edited message is fetched before use', async () => {
  const { mirror, posted } = setup();
  const full = msg({ id: 'p1' });
  await mirror.onCreate(full);
  const partial = { id: 'p1', partial: true, fetch: async () => msg({ id: 'p1', content: 'fetched' }) };
  await mirror.onUpdate(partial, partial);
  assert.equal([...posted.values()][0].payload.embeds[0].description, 'fetched');
});

test('a delete removes the copy and forgets the link; unknown deletes are ignored', async () => {
  const { mirror, posted } = setup();
  await mirror.onCreate(msg());
  await mirror.onDelete({ id: 'unknown', channelId: CH });
  await mirror.onDelete({ id: 'm1', channelId: CH });
  assert.equal([...posted.values()][0].deleted, true);
  assert.equal(mirror.links.m1, undefined);
  assert.equal(mirror.stats.deleted, 1);
});

test('links survive a restart, so edits still find their copy', async () => {
  const { mirror, store, logChannels, posted } = setup();
  await mirror.onCreate(msg());
  const again = new MessageMirror({ store, logChannels, config: { mirrorChannelId: CH, mirrorUserId: USER }, log: quiet });
  await again.load();
  await again.onUpdate(msg(), msg({ content: 'after restart' }));
  assert.equal(posted.size, 1);
  assert.equal([...posted.values()][0].payload.embeds[0].description, 'after restart');
});

test('a failed post is recorded, does not throw, and does not block later messages', async () => {
  const { mirror, logChannels, posted } = setup();
  const real = logChannels.send;
  logChannels.send = async () => { throw new Error('Missing Permissions'); };
  const a = msg();
  await mirror.onCreate(a);
  assert.deepEqual(a.reactions, []);
  assert.match(mirror.stats.lastError, /Missing Permissions/);
  logChannels.send = real;
  await mirror.onCreate(msg({ id: 'm9' }));
  assert.equal(posted.size, 1);
});

test('embed: long text is clipped, attachments listed, first image shown', () => {
  const e = mirrorEmbed(msg({
    content: 'x'.repeat(9000),
    attachments: [{ name: 'a.png', url: 'https://cdn/a.png', contentType: 'image/png' }, { name: 'b.zip', url: 'https://cdn/b.zip' }],
  }));
  assert.ok(e.description.length <= 4000);
  assert.equal(e.image.url, 'https://cdn/a.png');
  assert.equal(mirrorEmbed(msg({ content: '' })).description, '*(empty message)*');
  assert.equal(mirrorEmbed(msg(), { edited: true }).footer.text, 'Edited');
});
