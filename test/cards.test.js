'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { parseMessageRef } = require('../src/lib/messageRef');
const { tilePositions } = require('../src/lib/watermark');
const { wrapText } = require('../src/lib/messageCard');
const { encode, toBody } = require('../src/lib/discordRaw');

let hasCanvas = true;
try { require.resolve('@napi-rs/canvas'); } catch { hasCanvas = false; }

test('parseMessageRef reads links and bare ids, rejects junk', () => {
  assert.deepEqual(parseMessageRef('https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333'), { guildId: '111111111111111111', channelId: '222222222222222222', messageId: '333333333333333333' });
  assert.deepEqual(parseMessageRef('333333333333333333'), { guildId: null, channelId: null, messageId: '333333333333333333' });
  assert.equal(parseMessageRef('hello'), null);
  assert.equal(parseMessageRef('https://evil.example/channels/1/2/3'), null);
});

test('watermark tiles cover the whole image, rows staggered', () => {
  const p = tilePositions(720, 300, 200, 80);
  assert.ok(p.some((t) => t.x < 0) && p.some((t) => t.x > 720));
  assert.ok(p.some((t) => t.y < 0) && p.some((t) => t.y > 300));
  const gap = Math.max(...p.filter((t) => t.y === p[0].y).map((t) => t.x)) - Math.min(...p.filter((t) => t.y === p[0].y).map((t) => t.x));
  assert.ok(gap > 720);
});

test('wrapText wraps, breaks long words and caps the lines', () => {
  const measure = (s) => s.length * 10;
  assert.deepEqual(wrapText(measure, 'aaa bbb ccc', 70), ['aaa bbb', 'ccc']);
  assert.ok(wrapText(measure, 'x'.repeat(40), 50).every((l) => l.length <= 5));
  const capped = wrapText(measure, Array(50).fill('word').join(' '), 100, 3);
  assert.equal(capped.length, 3);
  assert.ok(capped[2].endsWith('...'));
});

test('files are sent as multipart, plain replies stay JSON', () => {
  assert.equal(encode({ a: 1 }).payload, '{"a":1}');
  const withFile = encode({ attachments: [] }, [{ attachment: Buffer.from('x'), name: 'a.png' }]);
  assert.ok(withFile.payload instanceof FormData);
  assert.deepEqual(toBody({ files: [{ attachment: Buffer.from('x'), name: 'a.png' }] }).attachments, [{ id: 0, filename: 'a.png' }]);
});

test('cards render to PNG, with and without the watermark', { skip: !hasCanvas && '@napi-rs/canvas not installed' }, async () => {
  const { renderMessageCard } = require('../src/lib/messageCard');
  for (const watermark of [false, true]) {
    const png = await renderMessageCard({ name: 'Captain Nova', text: 'Boarding starts in five minutes, please be seated.', color: 'green', time: 'Today', footer: 'Quoted from #general' }, { watermark });
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
  }
});
