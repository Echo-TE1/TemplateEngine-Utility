'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { JsonStore } = require('../src/services/store');

test('JsonStore keeps named documents between instances', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'store-'));
  const a = new JsonStore(dir);
  assert.deepEqual(await a.getDoc('x'), {});
  await a.setDoc('x', { users: ['1'] });
  await a.setDoc('y', { n: 2 });
  const b = new JsonStore(dir);
  assert.deepEqual(await b.getDoc('x'), { users: ['1'] });
  assert.deepEqual(await b.getDoc('y'), { n: 2 });
});
