const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

test('publication needs review and consent; unconfirmed retries keep their original snapshot, reference and race', async () => {
  const expected = { feature_id: 'feature', season: 2026, lineup: [{ id: 'grid', entry_id: 'entry' }], finishers: [{ id: 'finish', entry_id: 'entry', position: 1, driver_id: 'driver', name: 'Alex', number: '19X', color: '#fff' }], official: [] };
  const submit = { disabled: false, dataset: { label: 'Publish official results', unavailable: 'false' } };
  const ack = { checked: false }, feedback = {}, refresh = {};
  const card = {
    dataset: { raceId: 'friday', raceName: 'Friday night', missing: '2', snapshot: JSON.stringify(expected) },
    querySelector: selector => ({ '[data-official-publish]': submit, '[data-official-refresh]': refresh, '[role="status"]': feedback, input: ack })[selector]
  };
  const elements = { 'rn-official': card, 'race-list': { innerHTML: '' } };
  const requests = [], removed = [];
  let consent = false, lost = true;
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'test-staff' }) } },
    document: { getElementById: id => elements[id], querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, URL, crypto: require('node:crypto'), setTimeout, clearTimeout,
    localStorage: { removeItem: key => removed.push(key) }, confirm: () => consent,
    fetch: async (url, options = {}) => {
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
      if (!options.method) return reply([]);
      const request = JSON.parse(options.body);
      requests.push({ url, request });
      if (lost) return reply({ message: 'Response unavailable' }, 403);
      return reply({ race_id: request.p_race_id, client_ref: request.p_client_ref, posted_count: 1 });
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  scope.submit = submit;
  const run = code => runInNewContext(code, scope);
  run("RN.raceId = 'friday'");
  await run('rnPostOfficial(submit)');
  assert.equal(requests.length, 0, 'Unchecked review never posts');
  ack.checked = true;
  await run('rnPostOfficial(submit)');
  assert.equal(requests.length, 0, 'Cancelled consent never posts');
  consent = true;
  await run('rnPostOfficial(submit)');
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].request.p_expected, expected);
  assert.match(feedback.textContent, /Publication not confirmed/);
  assert.equal(removed.length, 0, 'Qualifying drafts survive an uncertain outcome');
  assert.equal(submit.textContent, 'Verify / retry publication');
  assert.equal(submit.disabled, false);
  // A new board snapshot or selected race cannot change an already reviewed retry.
  card.dataset.snapshot = '{}';
  ack.checked = false;
  lost = false;
  const retry = run('rnPostOfficial(submit)');
  const duplicateTap = run('rnPostOfficial(submit)');
  run("RN.raceId = 'saturday'");
  await Promise.all([retry, duplicateTap]);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].request, requests[0].request);
  assert.ok(requests.every(r => r.url.endsWith('/rpc/post_official_results')));
  assert.deepEqual(removed, ['rn_qual_friday', 'rn_qgroups_friday']);
});
