const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function form() {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, { value: '', checked: false, innerHTML: '', setAttribute() {} });
    return elements.get(id);
  };
  const drivers = [{ id: 'alex', name: 'Alex', number: '19X' }, { id: 'morgan', name: 'Morgan', number: '42' }];
  const writes = [];
  let lost = false, consent = true;
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'test-staff' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : el(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, URL, crypto: require('node:crypto'), setTimeout, clearTimeout,
    confirm: () => consent,
    fetch: async (url, options = {}) => {
      const table = new URL(url).pathname.split('/').pop();
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
      if (options.method) {
        const body = JSON.parse(options.body);
        writes.push({ table, body });
        return lost ? reply({ message: 'Response unavailable' }, 403) : reply({ result_id: 'new-result', race_id: body.p_race_id, driver_id: body.p_result.driver_id, client_ref: body.p_client_ref, position: body.p_result.position, points: body.p_result.points ?? 32, lap_time: body.p_result.lap_time });
      }
      return reply(table === 'drivers' ? drivers : table === 'races' ? [{ id: 'friday', name: 'Friday', race_date: '2026-10-23', season: 2026, status: 'past' }] : table === 'results' ? [{ id: 'old', driver_id: 'alex', position: 1, points: 35, season: 2026, lap_time: '12.1' }] : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  return { el, writes, run: code => runInNewContext(code, scope), lost: value => { lost = value; }, consent: value => { consent = value; } };
}

test('manual results reject invalid and occupied finishes, validate lap formats, and preserve explicit zero custom points', async () => {
  const app = form();
  app.el('res-race').value = 'friday';
  await app.run('loadManualResults()');
  assert.equal(app.el('res-pos').value, '2');
  assert.equal(app.el('res-pts').value, '32');
  assert.match(app.el('res-saved').innerHTML, /Alex/);
  app.el('res-driver').value = 'morgan';
  for (const value of ['0', '-1', '2nd', '1.5', '1001', '1']) {
    app.el('res-pos').value = value;
    await app.run('addResult()');
    assert.equal(app.el('res-save').disabled, true);
  }
  app.el('res-pos').value = '2';
  app.el('res-driver').value = 'alex';
  await app.run('addResult()');
  assert.match(app.el('res-validation').textContent, /already has a saved result/);
  app.el('res-driver').value = 'morgan';
  for (const value of ['0', '0:00', '1:60', '12fast', 'Infinity', '1:4.2']) {
    app.el('res-time').value = value;
    await app.run('addResult()');
    assert.equal(app.el('res-save').disabled, true);
  }
  assert.equal(app.writes.length, 0);
  app.el('res-time').value = '1:04.2';
  app.el('res-custom').checked = true;
  app.el('res-pts').value = '0';
  await app.run('addResult()');
  assert.deepEqual(app.writes[0].body.p_result, { driver_id: 'morgan', position: 2, lap_time: '1:04.2', points: 0 });
  assert.match(app.el('res-feedback').textContent, /Result confirmed/);
});

test('manual result consent, double taps and uncertain retries issue only one stable RPC request', async () => {
  const app = form();
  app.el('res-race').value = 'friday';
  await app.run('loadManualResults()');
  app.el('res-driver').value = 'morgan';
  app.el('res-time').value = '12.345';
  app.consent(false);
  await app.run('addResult()');
  assert.equal(app.writes.length, 0);
  app.consent(true); app.lost(true);
  await Promise.all([app.run('addResult()'), app.run('addResult()')]);
  assert.equal(app.writes.length, 1);
  assert.equal(app.el('res-time').value, '12.345');
  assert.match(app.el('res-feedback').textContent, /Save not confirmed/);
  assert.equal(app.el('res-time').disabled, true);
  assert.equal(app.el('res-save').disabled, false);
  app.lost(false);
  app.el('res-race').value = 'saturday';
  await app.run('addResult()');
  assert.equal(app.writes.length, 2);
  assert.ok(app.writes.every(w => w.table === 'add_manual_result'));
  assert.deepEqual(app.writes[0].body, app.writes[1].body);
  assert.equal(app.writes[1].body.p_result.points, null, 'Standard points are derived by the server');
  assert.equal(app.writes[1].body.p_expected.official[0].lap_time, '12.1');
  assert.match(app.el('res-feedback').textContent, /Result confirmed/);
  assert.equal(app.el('res-next').hidden, false);
});
