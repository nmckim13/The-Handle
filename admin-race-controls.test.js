const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function racePage() {
  const results = [{ id: 'first', session_id: 'heat', entry_id: 'jordan', finishing_position: 1 }];
  const writes = [], body = { innerHTML: '' };
  const page = { results, writes, failure: null };
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'test-staff' }) } },
    document: { getElementById: id => id === 'rn-body' ? body : null, querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, localStorage: { getItem: () => null },
    crypto: require('node:crypto'), URL, setTimeout, clearTimeout,
    fetch: async (input, options = {}) => {
      const url = new URL(input), table = url.pathname.split('/').pop();
      const reply = (value, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(value), text: async () => JSON.stringify(value) });
      if (options.method === 'POST') {
        const row = JSON.parse(options.body);
        writes.push(row);
        // Both callers can read the old position before an unqueued insert lands.
        await new Promise(resolve => setTimeout(resolve, 5));
        if (page.failure !== 'denied') results.push({ ...row, id: 'result-' + results.length });
        if (page.failure) return reply('Save not confirmed', 403);
        return reply([row]);
      }
      if (table === 'races') return reply([{ id: 'friday', name: 'Friday night' }]);
      if (table !== 'session_results') return reply([]);
      let rows = results.filter(r => r.session_id === url.searchParams.get('session_id')?.slice(3));
      if (url.searchParams.get('order')?.endsWith('desc')) rows = rows.slice().sort((a, b) => b.finishing_position - a.finishing_position);
      if (url.searchParams.get('limit')) rows = rows.slice(0, Number(url.searchParams.get('limit')));
      return reply(rows);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(match => match[1].trim())[1];
  runInNewContext(script, scope);
  page.run = code => runInNewContext(code, scope);
  page.run("RN.raceId = 'friday'");
  return page;
}

test('rapid finish taps preserve click order, ignore a double tap, and keep sessions independent', async () => {
  const page = racePage();
  await Promise.all([
    page.run("rnTapFinish('heat', 'morgan')"),
    page.run("rnTapFinish('heat', 'alex')"),
    page.run("rnTapFinish('heat', 'morgan')"),
    page.run("rnTapFinish('feature', 'alex')")
  ]);
  assert.deepEqual(page.results.filter(r => r.session_id === 'heat').map(r => [r.entry_id, r.finishing_position]), [
    ['jordan', 1], ['morgan', 2], ['alex', 3]
  ]);
  assert.deepEqual(page.results.filter(r => r.session_id === 'feature').map(r => [r.entry_id, r.finishing_position]), [['alex', 1]]);
});

test('an unconfirmed save stops later finish taps; retries neither duplicate nor reorder finishers', async () => {
  for (const failure of ['denied', 'response-lost']) {
    const page = racePage();
    page.failure = failure;
    await Promise.all([
      page.run("rnTapFinish('heat', 'alex')"),
      page.run("rnTapFinish('heat', 'morgan')")
    ]);
    assert.equal(page.results.some(r => r.entry_id === 'morgan'), false, failure);
    assert.equal(page.writes.length, 1, failure);
    page.failure = null;
    await page.run("rnTapFinish('heat', 'alex')");
    if (failure === 'denied') assert.equal(page.writes[1].client_ref, page.writes[0].client_ref);
    else assert.equal(page.writes.length, 1, 'A landed finish is recognized before another insert');
    await page.run("rnTapFinish('heat', 'morgan')");
    assert.deepEqual(page.results.map(r => [r.entry_id, r.finishing_position]), [['jordan', 1], ['alex', 2], ['morgan', 3]], failure);
  }
});
