'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { parseItems, diffMarket, searchItems, sparkline, matchesKeywords, priceHistory } = require('../src/lib/economy');

const make = (n, over = {}) => {
  const items = {};
  for (let i = 1; i <= n; i++) items[String(i)] = [`Item ${i}`, '', 5000, 6000, -1, 1, 1, -1, -1, -1];
  return { success: true, items: { ...items, ...over } };
};

test('parseItems reads rows and treats -1 as unset', () => {
  const m = parseItems(make(150, { 9999: ['Dominus', 'DOM', 100000, -1, -1] }));
  assert.equal(m.get('9999').rap, 100000);
  assert.equal(m.get('9999').value, null);
  assert.equal(m.size, 151);
});

test('parseItems rejects bad answers', () => {
  assert.throws(() => parseItems({ success: false }));
  assert.throws(() => parseItems(make(5)));
});

test('diffMarket finds movers, new items and ignores small or cheap changes', () => {
  const prev = parseItems(make(150, { 3: ['Item 3', '', 500, -1] }));
  const next = parseItems(make(150, { 1: ['Item 1', '', 6000, 6000], 2: ['Item 2', '', 5100, 6000], 3: ['Item 3', '', 300, -1], 777: ['New One', '', 2000, -1] }));
  const d = diffMarket(prev, next);
  assert.deepEqual(d.movers.map((m) => m.id), ['1']);
  assert.equal(d.movers[0].pct, 20);
  assert.deepEqual(d.newItems.map((i) => i.id), ['777']);
});

test('diffMarket treats mass changes as bad data', () => {
  const prev = parseItems(make(150));
  const nextRaw = make(150);
  for (const k of Object.keys(nextRaw.items)) nextRaw.items[k][2] = 9000;
  const d = diffMarket(prev, parseItems(nextRaw));
  assert.equal(d.skipped, true);
  assert.equal(d.movers.length, 0);
});

test('searchItems matches id, prefix and substring', () => {
  const m = parseItems(make(150, { 9999: ['Dominus Empyreus', 'DE', 1, 1] }));
  assert.equal(searchItems(m, '9999')[0].name, 'Dominus Empyreus');
  assert.equal(searchItems(m, 'domin')[0].id, '9999');
  assert.equal(searchItems(m, 'empyr')[0].id, '9999');
  assert.deepEqual(searchItems(m, ''), []);
});

test('sparkline and keywords and history', () => {
  assert.equal(sparkline([1, 8]), '▁█');
  assert.equal(sparkline([5]), '');
  assert.equal(matchesKeywords('New Robux pricing', ['robux']), true);
  assert.equal(matchesKeywords('unlimited fun', ['limited']), false);
  const h = priceHistory({ priceDataPoints: [{ date: '2026-01-02', value: 120 }, { date: '2026-01-01', value: 100 }] });
  assert.deepEqual(h.prices, [100, 120]);
  assert.equal(h.change, 20);
  assert.deepEqual(priceHistory(null), { prices: [], change: null });
});
