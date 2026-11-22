'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { profileCard, errorCard, IS_COMPONENTS_V2, EPHEMERAL } = require('../src/lib/profileCard');
const { BadgeEmojis } = require('../src/services/badgeEmojis');
const { reply } = require('../src/lib/respond');

const quiet = { log() {}, warn() {}, error() {} };
const base = {
  id: '156', name: 'builderman', displayName: 'builderman', verified: true, banned: false, description: 'Welcome to Roblox!',
  created: '2006-03-09T00:00:00Z', avatarUrl: 'https://tr.rbxcdn.com/a.png', followers: 69898106, following: 67852811, friends: 1,
  badges: [{ id: 1, name: 'Administrator', imageUrl: 'https://i/1.png' }, { id: 2, name: 'Friendship', imageUrl: 'https://i/2.png' }],
  rolimons: { private: false, premium: true, rap: 13838430, value: 34656295, lastOnline: '2026-10-01T00:00:00Z' },
  presence: null, rateLimited: false, url: 'https://www.roblox.com/users/156/profile',
};
const flat = (card) => JSON.stringify(card);

test('card is an ephemeral Components V2 container: heading, divider, details with avatar on the right, buttons', () => {
  const card = profileCard(base, { verified: '<:verified:9>', badges: ['<:rb_1:11>', '<:rb_2:12>'] });
  assert.equal(card.flags & IS_COMPONENTS_V2, IS_COMPONENTS_V2);
  assert.equal(card.flags & EPHEMERAL, EPHEMERAL);
  assert.equal(card.embeds, undefined);
  const [container] = card.components;
  assert.equal(container.type, 17);
  const [heading, rule, details, , , buttons] = container.components;
  assert.equal(heading.content, '## [builderman](https://www.roblox.com/users/156/profile) <:verified:9>');
  assert.equal(rule.type, 14);
  assert.equal(details.type, 9); // section: text + thumbnail accessory
  assert.equal(details.accessory.media.url, 'https://tr.rbxcdn.com/a.png');
  assert.deepEqual(buttons.components.map((b) => b.label), ['View Profile', 'Refresh']);
  assert.equal(buttons.components[0].url, base.url);
  assert.equal(buttons.components[1].custom_id, 'rp:refresh:156');

  const body = details.components[0].content;
  for (const line of ['**Following**: 67,852,811', '**Followers**: 69,898,106', '**Description**: Welcome to Roblox!', '**Badges (2)**: <:rb_1:11> <:rb_2:12>', '**ID**: 156']) {
    assert.ok(body.includes(line), line);
  }
  assert.ok(body.includes('**RAP**: [13,838,430](https://www.rolimons.com/player/156)')); // no inventory data: Rolimons fallback
  assert.ok(body.includes('**Value**: [34,656,295](https://www.rolimons.com/player/156)'));
  assert.ok(body.includes('**Last Online**: <t:'));
});

test('missing data says Unavailable (never invented); badges fall back to names; no avatar means plain text', () => {
  const card = profileCard({ ...base, avatarUrl: null, followers: null, following: null, badges: null, rolimons: null, description: '', displayName: 'Shown' });
  const text = flat(card);
  assert.ok(text.includes('**Followers**: Unavailable'));
  assert.ok(text.includes('**Badges**: Unavailable'));
  assert.ok(text.includes('**RAP**: Unavailable'));
  assert.ok(text.includes('**Last Online**: Unavailable'));
  assert.ok(text.includes('**Description**: No description'));
  assert.ok(text.includes('**Display Name**: Shown'));
  assert.equal(card.components[0].components[2].type, 10); // no section without an avatar

  const names = flat(profileCard(base, {})); // no emoji available
  assert.ok(names.includes('Administrator, Friendship'));
  assert.ok(names.includes('builderman](https://www.roblox.com/users/156/profile) ✓'));
});

test('private Rolimons inventories and a long description stay short and safe', () => {
  const text = flat(profileCard({ ...base, description: 'x'.repeat(5000), rolimons: { private: true }, inventory: { private: true } }));
  assert.ok(text.includes('**RAP**: Private'));
  assert.ok(text.includes('**Value**: Private'));
  assert.ok(text.length < 4000);
  assert.equal(errorCard('User not found', 'No such user').components[0].type, 17);
});

function fakeApp({ existing = [] } = {}) {
  const posts = [];
  const rest = {
    get: async () => ({ items: existing }),
    post: async (route, { body }) => { posts.push({ route, name: body.name, image: body.image }); return { id: String(100 + posts.length), name: body.name, animated: false }; },
  };
  return { client: { application: { id: 'APP' }, rest }, posts };
}
const png = async (url) => ({ status: url.includes('bad') ? 404 : 200, headers: new Map([['content-type', 'image/png']]), arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer });

test('BadgeEmojis uploads each badge once, reuses existing ones, and falls back to text on failures', async () => {
  const { client, posts } = fakeApp({ existing: [{ id: '5', name: 'rb_1', animated: false }, { id: '6', name: 'verified', animated: false }] });
  const emojis = new BadgeEmojis({ client, fetchImpl: png, log: quiet });
  const profile = { badges: [{ id: 1, name: 'A', imageUrl: 'https://i/1.png' }, { id: 2, name: 'B', imageUrl: 'https://i/2.png' }, { id: 3, name: 'C', imageUrl: 'https://bad/3.png' }, { name: 'no id' }] };
  const out = await emojis.forProfile(profile);
  assert.deepEqual(out.badges, ['<:rb_1:5>', '<:rb_2:101>', null, null]);
  assert.equal(out.verified, '<:verified:6>');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].route, '/applications/APP/emojis');
  assert.ok(posts[0].image.startsWith('data:image/png;base64,'));

  await emojis.forProfile(profile); // second time: nothing re-uploaded, the failed one is not retried yet
  assert.equal(posts.length, 1);
});

test('BadgeEmojis never throws: if Discord is unreachable the badges are just null', async () => {
  const client = { application: { id: 'APP' }, rest: { get: async () => { throw new Error('down'); } } };
  const out = await new BadgeEmojis({ client, fetchImpl: png, log: quiet }).forProfile({ badges: [{ id: 1 }, { id: 2 }] });
  assert.deepEqual(out, { verified: null, premium: null, badges: [null, null] });
});

test('reply() sends the answer straight to the callback endpoint (update = type 7), with discord.js as the fallback', async () => {
  const calls = [];
  const ok = async (url, o) => { calls.push({ url, body: JSON.parse(o.body) }); return { status: 200, text: async () => '' }; };
  const i = { id: 'I', token: 'T', customId: 'rp:x' };
  await reply(i, { flags: 1 << 15, components: [] }, { fetchImpl: ok, log: quiet });
  await reply(i, { components: [] }, { update: true, fetchImpl: ok, log: quiet });
  assert.equal(calls[0].url, 'https://discord.com/api/v10/interactions/I/T/callback');
  assert.equal(calls[0].body.type, 4);
  assert.equal(calls[0].body.data.flags, 1 << 15);
  assert.equal(calls[1].body.type, 7);

  let used = false;
  await reply({ ...i, reply: async () => { used = true; } }, { content: 'x' }, { fetchImpl: async () => { throw new Error('x'); }, log: quiet });
  assert.equal(used, true);
});

test('untracked Rolimons accounts say so; online users show Online now; the Rolimons source is only credited when it has data', () => {
  const text = flat(profileCard({ ...base, rolimons: { tracked: false, private: false, rap: null, value: null, lastOnline: null }, badges: [], presence: { status: 'Online', online: true, lastOnline: null } }));
  assert.ok(text.includes('**RAP**: Unavailable'));
  assert.ok(text.includes('**Value**: No data on Rolimons'));
  assert.ok(text.includes('**Last Online**: Online now'));
  assert.ok(text.includes('**Badges (0)**: None'));
  assert.ok(text.includes('-# Roblox'));
  assert.ok(!text.includes('Rolimons ·') && !text.includes('· Rolimons'));
});

test('RAP comes from the Roblox inventory: a number, "At least" when capped, Private when hidden', () => {
  const rap = (inventory) => flat(profileCard({ ...base, inventory, rolimons: null }));
  assert.ok(rap({ private: false, rap: 1234, items: 3, complete: true }).includes('**RAP**: 1,234'));
  assert.ok(rap({ private: false, rap: 0, items: 0, complete: true }).includes('**RAP**: 0'));
  assert.ok(rap({ private: false, rap: 99999, items: 500, complete: false }).includes('**RAP**: At least 99,999'));
  assert.ok(rap({ private: true, rap: null }).includes('**RAP**: Private'));
});
