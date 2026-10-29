const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

test('public admin opens without an account and loads only after page initialization', async () => {
  const panel = { hidden: true }, pending = [];
  const scope = { window: {}, document: { getElementById: () => panel }, setTimeout: fn => pending.push(fn) };
  runInNewContext(readFileSync(`${__dirname}/admin-auth.js`, 'utf8'), scope);
  let initialized = false, loads = 0;
  const client = scope.window.AdminAuth.create({ key: 'public-key', onAuthorized() {
    assert.equal(initialized, true);
    loads++;
  } });
  assert.equal(panel.hidden, false);
  assert.equal(loads, 0);
  initialized = true;
  pending.shift()();
  assert.equal(loads, 1);
  assert.equal(await client.getAccessToken(), 'public-key');
});
