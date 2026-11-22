'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { toBody } = require('../src/lib/discordRaw');
const { editReply } = require('../src/lib/respond');

const quiet = { log() {}, error() {} };
const embed = { toJSON: () => ({ title: 'T' }) };
const row = { toJSON: () => ({ type: 1, components: [] }) };

function interaction(over = {}) {
  return { commandName: 'x', applicationId: 'APP', token: 'TOK', ephemeral: true, editReply: async () => {}, ...over };
}
const ok = (calls) => async (url, options) => { calls.push({ url, method: options.method, body: JSON.parse(options.body) }); return { status: 200, text: async () => '' }; };

test('toBody converts builders and camelCase fields to what Discord expects', () => {
  assert.deepEqual(toBody({ embeds: [embed], components: [row], allowedMentions: { parse: [] }, content: 'hi' }), {
    content: 'hi', embeds: [{ title: 'T' }], components: [{ type: 1, components: [] }], allowed_mentions: { parse: [] },
  });
});

test('the direct request is tried first and uses the interaction webhook', async () => {
  const calls = [];
  let used = false;
  await editReply(interaction({ editReply: async () => { used = true; } }), { embeds: [embed] }, { fetchImpl: ok(calls), log: quiet });
  assert.equal(calls[0].method, 'PATCH');
  assert.equal(calls[0].url, 'https://discord.com/api/v10/webhooks/APP/TOK/messages/@original');
  assert.deepEqual(calls[0].body.embeds, [{ title: 'T' }]);
  assert.equal(used, false);
});

test('if the direct request fails, interaction.editReply is used', async () => {
  let used = false;
  const failing = async () => { throw new Error('network down'); };
  await editReply(interaction({ editReply: async () => { used = true; } }), { embeds: [embed] }, { fetchImpl: failing, log: quiet });
  assert.equal(used, true);
});

test('if both fail, a private follow-up is sent; if everything fails it throws instead of hanging', async () => {
  const calls = [];
  let n = 0;
  const flaky = async (url, options) => {
    n += 1;
    if (n === 1) return { status: 500, text: async () => 'boom' }; // direct PATCH
    calls.push({ url, method: options.method, body: JSON.parse(options.body) });
    return { status: 200, text: async () => '' };
  };
  await editReply(interaction({ editReply: async () => { throw new Error('stuck'); } }), { embeds: [embed] }, { fetchImpl: flaky, log: quiet });
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].url, 'https://discord.com/api/v10/webhooks/APP/TOK');
  assert.equal(calls[0].body.flags, 64);

  const dead = async () => ({ status: 404, text: async () => 'Unknown Webhook' });
  await assert.rejects(
    editReply(interaction({ editReply: async () => { throw new Error('stuck'); } }), { embeds: [embed] }, { fetchImpl: dead, log: quiet }),
    /Could not deliver/,
  );
});
