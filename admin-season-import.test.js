const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function importer() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', checked: false, style: {}, innerHTML: '', setAttribute() {}, scrollIntoView() {} });
    return elements.get(id);
  };
  const drivers = [{ id: 'driver-a', name: 'Alex, "Ace"', number: '19X' }, { id: 'driver-b', name: 'Morgan', number: '42' }];
  const saved = [{ id: 'old', driver_id: 'driver-a', position: 1, points: 35, season: 2026, lap_time: '12.5' }];
  const writes = [];
  let consent = true, lost = false;
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'staff-test' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : element(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, URL, crypto: require('node:crypto'), setTimeout, clearTimeout,
    confirm: () => consent,
    fetch: async (url, options = {}) => {
      const table = new URL(url).pathname.split('/').pop();
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });
      if (options.method) {
        const body = JSON.parse(options.body);
        writes.push({ url, body });
        return lost ? reply({ message: 'Response unavailable' }, 403) : reply({ race_id: body.p_race_id, client_ref: body.p_client_ref, imported_count: body.p_rows.length });
      }
      return reply(table === 'drivers' ? drivers : table === 'results' ? saved : table === 'races' ? [{ id: 'friday', name: 'Friday', race_date: '2026-10-30', season: 2026 }] : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  const run = code => runInNewContext(code, scope);
  return {
    element, writes, run, saved,
    consent: value => { consent = value; }, lost: value => { lost = value; },
    async upload(text) {
      element('import-race').value = 'friday';
      scope.event = { target: { value: 'file', files: [{ name: 'results.csv', size: text.length, text: async () => text }] } };
      await run('handleCSVUpload(event)');
    }
  };
}

test('invalid season sheets cannot clear saved results; quoted CSV and blank roster positions round-trip', async () => {
  const app = importer();
  for (const csv of [
    'Position,Driver Name\n,Morgan',
    'Position,Driver Name\n1x,Morgan',
    'Position,Driver Name\n1,Morgan\n1,Morgan',
    'Position,Driver Name\n2,Morgan',
    'Position,Driver Name\n1,Unknown',
    'Position,Driver Name,Driver Number\n1,Morgan,WRONG',
    'Position,Driver Name\n1,"Morgan',
    'Position,Driver Name\n1,Morgan,extra'
  ]) {
    await app.upload(csv);
    app.element('import-ack').checked = true;
    await app.run('confirmImport()');
    assert.equal(app.element('import-submit').disabled, true);
    assert.match(app.element('import-preview-card').innerHTML, /Import blocked/);
  }
  assert.equal(app.writes.length, 0, 'Not even a DELETE is issued for rejected sheets');
  await app.upload('\uFEFFPosition,Driver Name,Driver Number,Points,Notes\r\n2,Morgan,42,999,"Second\r\nline"\r\n1,"Alex, ""Ace""",19X,999,First\r\n,Unused,88,,');
  assert.match(app.element('import-preview-card').innerHTML, /1 row\(s\) with blank Position excluded/);
  assert.match(app.element('import-preview-card').innerHTML, /Second\nline/);
  await app.run('confirmImport()');
  assert.deepEqual(app.writes[0].body.p_rows, [{ driver_id: 'driver-a', position: 1 }, { driver_id: 'driver-b', position: 2 }]);
  assert.equal(app.writes[0].body.p_expected.official[0].lap_time, '12.5');
});

test('season import requires consent and retries the original reviewed race and request after an uncertain response', async () => {
  const app = importer();
  await app.upload('Position,Driver Name\n1,Morgan');
  await app.run('confirmImport()');
  assert.equal(app.writes.length, 0);
  app.element('import-ack').checked = true;
  app.consent(false);
  await app.run('confirmImport()');
  assert.equal(app.writes.length, 0);
  app.consent(true); app.lost(true);
  await app.run('confirmImport()');
  assert.match(app.element('import-feedback').textContent, /Import not confirmed/);
  app.element('import-race').value = 'saturday';
  app.element('import-ack').checked = false;
  app.saved[0].position = 9;
  app.lost(false);
  await Promise.all([app.run('confirmImport()'), app.run('confirmImport()')]);
  assert.equal(app.writes.length, 2);
  assert.deepEqual(app.writes[1].body, app.writes[0].body);
  assert.ok(app.writes.every(w => w.url.endsWith('/rpc/import_season_results')));
  assert.equal(app.writes[1].body.p_race_id, 'friday');
  assert.match(app.element('import-feedback').textContent, /Import confirmed: 1 results saved/);
});
