'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { startWatchdog } = require('../src/lib/watchdog');

test('watchdog restarts only after being offline for the whole limit', async () => {
  let t = 0;
  let ready = true;
  let stuck = 0;
  const log = { log() {}, warn() {}, error() {} };
  const w = startWatchdog({ isReady: () => ready, onStuck: () => stuck++, maxDownMs: 1000, everyMs: 5, now: () => t, log });
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(stuck, 0);
  ready = false;
  t = 100;
  await new Promise((r) => setTimeout(r, 20));
  t = 900;
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(stuck, 0);
  ready = true; // came back in time
  await new Promise((r) => setTimeout(r, 20));
  ready = false;
  t = 2000;
  await new Promise((r) => setTimeout(r, 20));
  t = 3100;
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(stuck >= 1);
  w.stop();
});
