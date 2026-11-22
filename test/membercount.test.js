'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { MemberCountNote } = require('../src/services/memberCount');

const quiet = { log() {}, warn() {}, error() {} };

function fakeClient(counts) {
  const patches = [];
  const gets = [];
  return {
    patches, gets,
    rest: {
      get: async (route, opts) => {
        gets.push({ route, query: String(opts.query) });
        const next = counts.shift();
        if (next instanceof Error) throw next;
        return { approximate_member_count: next };
      },
      patch: async (route, opts) => { patches.push({ route, body: opts.body }); },
    },
  };
}

test('sets the note to the member count and only edits when the number changes', async () => {
  const client = fakeClient([1234, 1234, 1300]);
  const note = new MemberCountNote({ client, guildId: '99', log: quiet });
  assert.equal(await note.update(), true);
  assert.deepEqual(client.patches[0], { route: '/applications/@me', body: { description: 'Watching over 1,234 members' } });
  assert.equal(client.gets[0].route, '/guilds/99');
  assert.equal(client.gets[0].query, 'with_counts=true');
  assert.equal(await note.update(), false); // unchanged: no edit, no rate-limit use
  assert.equal(client.patches.length, 1);
  assert.equal(await note.update(), true);
  assert.equal(client.patches[1].body.description, 'Watching over 1,300 members');
  assert.equal(note.stats.count, 1300);
});

test('errors are recorded, never thrown, and a later check recovers', async () => {
  const notIn = Object.assign(new Error('Unknown Guild'), { code: 10004 });
  const client = fakeClient([notIn, 50]);
  const note = new MemberCountNote({ client, guildId: '99', log: quiet });
  assert.equal(await note.update(), false);
  assert.match(note.stats.error, /Unknown Guild.*in that server/);
  assert.equal(client.patches.length, 0);
  assert.equal(await note.update(), true);
  assert.equal(note.stats.error, null);
});

test('a failed edit is retried next time instead of being remembered as done', async () => {
  const client = fakeClient([10, 10]);
  let fail = true;
  client.rest.patch = async () => { if (fail) throw new Error('rate limited'); };
  const note = new MemberCountNote({ client, guildId: '99', log: quiet });
  assert.equal(await note.update(), false);
  fail = false;
  assert.equal(await note.update(), true);
});

test('a bad answer from Discord is not turned into a number', async () => {
  const client = { rest: { get: async () => ({}), patch: async () => assert.fail('should not edit') } };
  const note = new MemberCountNote({ client, guildId: '99', log: quiet });
  assert.equal(await note.update(), false);
  assert.match(note.stats.error, /did not return/);
});
