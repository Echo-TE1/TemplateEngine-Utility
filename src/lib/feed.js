'use strict';

// A small, dependency-free reader for RSS 2.0 and Atom feeds, plus the "which items are new" logic.

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', hellip: '…', ndash: '–', mdash: '—', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“' };

function decode(text) {
  return String(text ?? '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => safeChar(Number.parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => safeChar(Number.parseInt(d, 10)))
    .replace(/&([a-z]+);/gi, (m, name) => ENTITIES[name.toLowerCase()] ?? m);
}

function safeChar(code) {
  try {
    return String.fromCodePoint(code);
  } catch {
    return '';
  }
}

/** HTML -> readable plain text. */
function toPlain(html) {
  return decode(
    decode(String(html ?? ''))
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/(p|div|li|h[1-6])>/gi, '\n')
      .replace(/<[^>]*>/g, ''),
  )
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n+/g, '\n')
    .trim();
}

// First <tag ...>content</tag> inside block (tag names may contain ":" e.g. content:encoded).
function tagText(block, tag) {
  const found = new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`, 'i').exec(block);
  return found ? decode(found[1]).trim() : '';
}

function allTags(block, tag) {
  const out = [];
  const re = new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`, 'gi');
  for (let m = re.exec(block); m; m = re.exec(block)) out.push(decode(m[1]).trim());
  return out.filter(Boolean);
}

function attr(tagSource, name) {
  const found = new RegExp(`${name}\\s*=\\s*["']([^"']*)["']`, 'i').exec(tagSource);
  return found ? decode(found[1]) : '';
}

function atomLink(block) {
  const links = [...block.matchAll(/<link\b[^>]*>/gi)].map((m) => m[0]);
  const alternate = links.find((l) => !/rel\s*=\s*["'](?!alternate)/i.test(l)) || links[0];
  return alternate ? attr(alternate, 'href') : '';
}

function firstImage(block) {
  const media = /<media:(?:content|thumbnail)\b[^>]*url\s*=\s*["']([^"']+)["']/i.exec(block);
  if (media) return decode(media[1]);
  const enclosure = /<enclosure\b[^>]*type\s*=\s*["']image[^"']*["'][^>]*url\s*=\s*["']([^"']+)["']/i.exec(block)
    || /<enclosure\b[^>]*url\s*=\s*["']([^"']+)["'][^>]*type\s*=\s*["']image/i.exec(block);
  if (enclosure) return decode(enclosure[1]);
  const body = tagText(block, 'content:encoded') || tagText(block, 'description') || tagText(block, 'content') || '';
  const img = /<img\b[^>]*src\s*=\s*["']([^"']+)["']/i.exec(decode(body));
  return img ? img[1] : '';
}

/**
 * Parses RSS 2.0 or Atom text into items (newest first as given by the feed):
 * [{ id, title, link, summary, published, tags, image }]
 * A truncated feed (we only download the first part) is fine: only complete items are returned.
 */
function parseFeed(xml) {
  const text = String(xml ?? '');
  const isAtom = /<feed\b/i.test(text) && !/<rss\b/i.test(text);
  const itemTag = isAtom ? 'entry' : 'item';
  const blocks = [...text.matchAll(new RegExp(`<${itemTag}\\b[\\s\\S]*?</${itemTag}>`, 'gi'))].map((m) => m[0]);

  return blocks
    .map((block) => {
      const title = toPlain(tagText(block, 'title'));
      const link = isAtom ? atomLink(block) : tagText(block, 'link');
      const id = tagText(block, isAtom ? 'id' : 'guid') || link || title;
      const published = tagText(block, isAtom ? 'published' : 'pubDate') || tagText(block, 'updated') || tagText(block, 'dc:date');
      const rawSummary = isAtom
        ? tagText(block, 'summary') || tagText(block, 'content')
        : tagText(block, 'description') || tagText(block, 'content:encoded');
      const tags = isAtom
        ? [...block.matchAll(/<category\b[^>]*term\s*=\s*["']([^"']+)["']/gi)].map((m) => decode(m[1]))
        : allTags(block, 'category').map(toPlain);
      return { id: id.trim(), title, link, summary: toPlain(rawSummary), published, tags: [...new Set(tags)].slice(0, 5), image: firstImage(block) };
    })
    .filter((item) => item.id && item.title);
}

/**
 * Decides which items to announce.
 *  seen      ids already handled (array)
 *  baseline  true on the very first read of a feed: remember everything, announce nothing
 *  maxPost   at most this many are announced per check (the rest are remembered silently)
 * Returns { fresh (oldest first), seen (newest ids first, capped) }.
 */
function findNew(seen, items, { baseline = false, maxPost = 5, keep = 300 } = {}) {
  const known = new Set(seen);
  const unseen = items.filter((item) => !known.has(item.id));
  const fresh = baseline ? [] : unseen.slice(0, maxPost).reverse(); // feeds are newest first: post oldest first
  const nextSeen = [...unseen.map((i) => i.id), ...seen].slice(0, keep);
  return { fresh, seen: nextSeen };
}

module.exports = { parseFeed, findNew, toPlain, decode };
