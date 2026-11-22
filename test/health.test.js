'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { evaluate, isOk, logFor } = require('../src/lib/health');

const T = (n) => `2026-11-21T10:0${n}:00.000Z`;

// Feeds a list of results (true = answered) and returns the changes in order.
function run(results, failAfter = 2) {
  let state;
  const changes = [];
  results.forEach((ok, i) => {
    const out = evaluate(state, ok, failAfter, T(i));
    state = out.next;
    changes.push(out.change);
  });
  return { state, changes };
}

test('anything below HTTP 500 counts as up (even 401/403/405/429); 5xx and no answer count as down', () => {
  for (const s of [200, 301, 403, 405, 429]) assert.equal(isOk(s), true, String(s));
  for (const s of [500, 502, 503, null, undefined]) assert.equal(isOk(s), false, String(s));
});

test('one failed check does not raise an alert; two in a row do, once', () => {
  const { changes } = run([true, false, true, false, false, false, false]);
  assert.deepEqual(changes, [null, null, null, null, 'down', null, null]);
});

test('recovery needs two good checks in a row, and is reported once', () => {
  const { changes } = run([true, false, false, true, false, true, true, true]);
  assert.deepEqual(changes, [null, null, 'down', null, null, null, 'up', null]);
});

test('a first-ever failure is not reported until the threshold is reached', () => {
  assert.deepEqual(run([false]).changes, [null]);
  assert.deepEqual(run([false, false]).changes, [null, 'down']);
});

test('a higher threshold is respected', () => {
  assert.deepEqual(run([true, false, false, false], 3).changes, [null, null, null, 'down']);
});

test('keeps when it went down so the recovery message can say how long', () => {
  const down = run([true, false, false]).state;
  assert.equal(down.status, 'down');
  assert.equal(down.downSince, T(2));
  const up = evaluate(evaluate(down, true, 2, T(3)).next, true, 2, T(4));
  assert.equal(up.change, 'up');
  assert.equal(up.next.lastDownSince, T(2));
  assert.equal(up.next.downSince, null);
});

test('Website goes to the Website Status log, everything else to API Status', () => {
  assert.equal(logFor('Website'), 'website_status');
  assert.equal(logFor(' website '), 'website_status');
  assert.equal(logFor('Users API'), 'api_status');
  assert.equal(logFor('Authentication'), 'api_status');
});
