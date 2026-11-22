'use strict';

// Pure logic for the Roblox economy tracker (no discord.js, no network).
// Market data comes from Rolimons' public item list: id -> [name, acronym, rap, value, default value, ...].
// Only the first five fields are used; the rest are ratings whose numeric meaning is not documented.

/** Rolimons item list JSON -> Map(id -> { id, name, acronym, rap, value }). Throws if it does not look right. */
function parseItems(json) {
  if (!json || json.success !== true || !json.items || typeof json.items !== 'object') {
    throw new Error('unexpected answer from Rolimons (no item list)');
  }
  const items = new Map();
  for (const [id, row] of Object.entries(json.items)) {
    if (!Array.isArray(row) || typeof row[0] !== 'string') continue;
    const positive = (v) => (Number.isFinite(v) && v > 0 ? v : null); // -1 means "not set"
    items.set(id, { id, name: row[0], acronym: row[1] || '', rap: positive(row[2]), value: positive(row[3]) });
  }
  if (items.size < 100) throw new Error(`only ${items.size} items in the answer, expected thousands`);
  return items;
}

const pct = (from, to) => ((to - from) / from) * 100;

/**
 * Compares two snapshots. A mover is an item whose RAP or value moved at least `movePercent` percent
 * (and is worth at least `minRap`, so cheap items do not make noise). New items are limiteds that were not
 * in the previous snapshot. If a huge share of the market "moves" at once it is treated as bad data.
 * @returns {{ movers, newItems, skipped }}
 */
function diffMarket(prev, next, { movePercent = 10, minRap = 1000 } = {}) {
  const movers = [];
  const newItems = [];
  for (const [id, now] of next) {
    const before = prev.get(id);
    if (!before) {
      newItems.push(now);
      continue;
    }
    let best = null;
    for (const [metric, key] of [['RAP', 'rap'], ['Value', 'value']]) {
      const from = before[key];
      const to = now[key];
      if (!from || !to || from === to) continue;
      if (Math.max(from, to) < minRap) continue;
      const change = pct(from, to);
      if (Math.abs(change) < movePercent) continue;
      if (!best || Math.abs(change) > Math.abs(best.pct)) best = { id, name: now.name, metric, from, to, pct: change };
    }
    if (best) movers.push(best);
  }
  if (movers.length > Math.max(50, next.size * 0.15)) return { movers: [], newItems: [], skipped: true };
  movers.sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct));
  return { movers, newItems, skipped: false };
}

/** Items matching a search: exact id, then name/acronym starts-with, then contains. */
function searchItems(items, query, limit = 25) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return [];
  if (items.has(q)) return [items.get(q)];
  const starts = [];
  const contains = [];
  for (const item of items.values()) {
    const name = item.name.toLowerCase();
    const acronym = item.acronym.toLowerCase();
    if (name.startsWith(q) || acronym === q) starts.push(item);
    else if (name.includes(q)) contains.push(item);
    if (starts.length >= limit) break;
  }
  return [...starts, ...contains].slice(0, limit);
}

const BLOCKS = '▁▂▃▄▅▆▇█';

/** [1, 5, 3] -> "▁█▄" (a tiny text chart). */
function sparkline(values) {
  const nums = values.filter(Number.isFinite);
  if (nums.length < 2) return '';
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  if (max === min) return BLOCKS[3].repeat(nums.length);
  return nums.map((v) => BLOCKS[Math.round(((v - min) / (max - min)) * (BLOCKS.length - 1))]).join('');
}

/** Does the text mention any keyword? Whole words, case-insensitive ("limited" does not match "unlimited"). */
function matchesKeywords(text, keywords) {
  const hay = String(text || '');
  return keywords.some((k) => new RegExp(`(^|[^a-z0-9])${k.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^a-z0-9]|$)`, 'i').test(hay));
}

/** Resale data -> last `days` daily prices, oldest first, and the change from first to last. */
function priceHistory(resale, days = 30) {
  const points = Array.isArray(resale && resale.priceDataPoints) ? resale.priceDataPoints : [];
  const prices = points
    .filter((p) => p && Number.isFinite(p.value) && Date.parse(p.date))
    .sort((a, b) => Date.parse(a.date) - Date.parse(b.date))
    .slice(-days)
    .map((p) => p.value);
  if (prices.length < 2 || !prices[0]) return { prices, change: null };
  return { prices, change: pct(prices[0], prices[prices.length - 1]) };
}

module.exports = { parseItems, diffMarket, searchItems, sparkline, matchesKeywords, priceHistory };
