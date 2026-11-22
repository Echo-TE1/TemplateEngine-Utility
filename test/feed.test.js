'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { parseFeed, findNew, toPlain } = require('../src/lib/feed');

const WORDPRESS = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Roblox Blog</title>
<item>
  <title><![CDATA[Roblox Summer Event: Join the &amp; fun]]></title>
  <link>https://blog.roblox.com/2026/11/summer-event/</link>
  <pubDate>Fri, 20 Nov 2026 10:00:00 +0000</pubDate>
  <guid isPermaLink="false">https://blog.roblox.com/?p=1002</guid>
  <category><![CDATA[Events]]></category><category><![CDATA[News]]></category>
  <description><![CDATA[<p>Our biggest event &amp; <b>more</b>.</p><img src="https://cdn.example.com/a.png">]]></description>
</item>
<item>
  <title>Platform update 1.2</title>
  <link>https://blog.roblox.com/2026/11/update/</link>
  <pubDate>Thu, 19 Nov 2026 10:00:00 +0000</pubDate>
  <guid>https://blog.roblox.com/?p=1001</guid>
  <description>Short text</description>
</item>
<item><title>Cut off mid-item (download was truncated)</title><link>https://x</link>`;

const DISCOURSE = `<?xml version="1.0"?><rss version="2.0" xmlns:discourse="http://www.discourse.org/" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>Updates</title>
<item><title>Weekly Recap: November 16 - 20, 2026</title>
<link>https://devforum.roblox.com/t/weekly-recap/5000001</link>
<description><![CDATA[<p>This week: new APIs</p>]]></description>
<pubDate>Fri, 20 Nov 2026 18:00:00 +0000</pubDate>
<guid isPermaLink="false">devforum.roblox.com-topic-5000001</guid>
<category>Updates</category></item></channel></rss>`;

const ATOM = `<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title>
<entry><title>Atom item</title><id>tag:example,2026:1</id>
<link rel="self" href="https://example.com/self"/><link rel="alternate" href="https://example.com/post"/>
<published>2026-11-20T10:00:00Z</published><summary type="html">&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;</summary>
<category term="events"/></entry></feed>`;

test('parses a WordPress RSS feed: CDATA, entities, categories, image; skips a truncated last item', () => {
  const items = parseFeed(WORDPRESS);
  assert.equal(items.length, 2);
  assert.equal(items[0].title, 'Roblox Summer Event: Join the & fun');
  assert.equal(items[0].link, 'https://blog.roblox.com/2026/11/summer-event/');
  assert.equal(items[0].id, 'https://blog.roblox.com/?p=1002');
  assert.equal(items[0].summary, 'Our biggest event & more.');
  assert.deepEqual(items[0].tags, ['Events', 'News']);
  assert.equal(items[0].image, 'https://cdn.example.com/a.png');
  assert.equal(items[1].summary, 'Short text');
});

test('parses a Discourse RSS feed', () => {
  const [item] = parseFeed(DISCOURSE);
  assert.equal(item.title, 'Weekly Recap: November 16 - 20, 2026');
  assert.equal(item.id, 'devforum.roblox.com-topic-5000001');
  assert.equal(item.summary, 'This week: new APIs');
  assert.deepEqual(item.tags, ['Updates']);
});

test('parses an Atom feed and picks the alternate link', () => {
  const [item] = parseFeed(ATOM);
  assert.equal(item.link, 'https://example.com/post');
  assert.equal(item.id, 'tag:example,2026:1');
  assert.equal(item.summary, 'Hello & welcome');
  assert.deepEqual(item.tags, ['events']);
});

test('garbage and empty input give no items instead of throwing', () => {
  assert.deepEqual(parseFeed(''), []);
  assert.deepEqual(parseFeed('<html><body>Not a feed</body></html>'), []);
  assert.deepEqual(parseFeed(null), []);
});

test('toPlain strips tags and scripts', () => {
  assert.equal(toPlain('<p>Hi <script>alert(1)</script><b>there</b></p><p>Bye</p>'), 'Hi there\nBye');
});

test('first read is a silent baseline; later only new items are announced, oldest first', () => {
  const [a, b, c] = ['a', 'b', 'c'].map((id) => ({ id, title: id }));
  const first = findNew([], [c, b, a], { baseline: true });
  assert.deepEqual(first.fresh, []);
  assert.deepEqual(first.seen, ['c', 'b', 'a']);

  const d = { id: 'd', title: 'd' };
  const e = { id: 'e', title: 'e' };
  const next = findNew(first.seen, [e, d, c, b, a]);
  assert.deepEqual(next.fresh.map((i) => i.id), ['d', 'e']);
  assert.deepEqual(findNew(next.seen, [e, d, c, b, a]).fresh, []);
});

test('a burst is capped, and the skipped items are still remembered (no repeat later)', () => {
  const items = ['n6', 'n5', 'n4', 'n3', 'n2', 'n1'].map((id) => ({ id, title: id }));
  const result = findNew([], items, { maxPost: 3 });
  assert.deepEqual(result.fresh.map((i) => i.id), ['n4', 'n5', 'n6']);
  assert.deepEqual(findNew(result.seen, items).fresh, []);
});

test('the remembered list is capped', () => {
  const items = Array.from({ length: 10 }, (_, i) => ({ id: `i${i}`, title: 't' }));
  assert.equal(findNew([], items, { baseline: true, keep: 4 }).seen.length, 4);
});
