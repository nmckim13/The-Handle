const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function roster() {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, { value: '', innerHTML: '', setAttribute() {}, focus() {}, scrollIntoView() {} });
    return elements.get(id);
  };
  const drivers = [
    { id: 'alex', name: 'Alex <img onerror=alert(1)>', number: '19X', color: '#abcdef', transponder_id: 18, starts: 10, wins: 3 },
    { id: 'morgan', name: 'Morgan', number: '07', color: '#123456', transponder_id: 0, starts: 0, wins: 0 },
    { id: 'taylor', name: 'Taylor', number: '32', color: '#F26419', transponder_id: null, starts: 0, wins: 0 }
  ];
  const writes = [], receipts = new Map();
  const state = { lost: false, failRoster: false, consent: true };
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'staff-test' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : el(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, crypto: require('node:crypto'), URL, setTimeout, clearTimeout,
    confirm: () => state.consent,
    fetch: async (url, options = {}) => {
      const u = new URL(url), table = u.pathname.split('/').pop();
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(body)), text: async () => JSON.stringify(body) });
      if (options.method) {
        const body = JSON.parse(options.body); writes.push({ table, body });
        assert.equal(table, 'manage_driver');
        if (!receipts.has(body.p_client_ref)) {
          const i = drivers.findIndex(d => d.id === body.p_driver_id);
          const action = body.p_driver === null ? 'removed' : body.p_expected === null ? 'created' : 'updated';
          if (action === 'removed') drivers.splice(i, 1);
          else if (i < 0) drivers.push({ id: body.p_driver_id, ...body.p_driver, starts: 0, wins: 0 });
          else Object.assign(drivers[i], body.p_driver);
          receipts.set(body.p_client_ref, { driver_id: body.p_driver_id, client_ref: body.p_client_ref, action, driver: body.p_driver === null ? null : { id: body.p_driver_id, ...body.p_driver } });
        }
        return state.lost ? reply({ message: 'Confirmation unavailable' }, 403) : reply(receipts.get(body.p_client_ref));
      }
      if (table === 'drivers') return state.failRoster && u.searchParams.get('select').includes('starts,wins') ? reply('Roster unavailable', 403) : reply(drivers);
      return reply(table === 'entries' && u.searchParams.get('driver_id') === 'eq.alex' ? [{ id: 'signup' }] : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  el('d-color').value = '#F26419';
  return { el, state, writes, drivers, run: code => runInNewContext(code, scope) };
}

test('driver search/filter retain missing and zero transponders; invalid and conflicting assignments cannot save', async () => {
  const app = roster(); await app.run('loadDriverList()');
  assert.match(app.el('driver-count').textContent, /3 of 3 drivers · 1 without/);
  assert.match(app.el('driver-list').innerHTML, /&lt;img/);
  assert.doesNotMatch(app.el('driver-list').innerHTML, /<img/);
  app.el('driver-search').value = '0'; app.run('renderDriverList()');
  assert.match(app.el('driver-list').innerHTML, /Morgan/);
  app.el('driver-search').value = ''; app.el('driver-filter').value = 'missing'; app.run('renderDriverList()');
  assert.match(app.el('driver-list').innerHTML, /Taylor/); assert.doesNotMatch(app.el('driver-list').innerHTML, /Morgan/);
  app.el('d-name').value = 'New Driver'; app.el('d-number').value = '19X';
  for (const raw of ['-1', '1.5', '18x', '2147483648', '01', 'Infinity', '18', '0']) {
    app.el('d-transponder').value = raw; await app.run('saveDriver()');
    assert.equal(app.el('d-save').disabled, true);
  }
  app.el('d-name').value = '  ALEX <img onerror=alert(1)> '; app.el('d-number').value = '19x';
  app.el('d-transponder').value = ''; await app.run('saveDriver()');
  assert.match(app.el('d-validation').textContent, /already in the roster/);
  assert.equal(app.writes.length, 0);
  app.el('d-name').value = 'New Driver'; app.el('d-number').value = '19X';
  app.el('d-transponder').value = '2147483647';
  await app.run('saveDriver()');
  assert.equal(app.writes[0].body.p_driver.number, '19X', 'Shared car numbers do not imply duplicate identities');
  assert.equal(app.writes[0].body.p_driver.transponder_id, 2147483647);
  assert.match(app.el('d-feedback').textContent, /created — confirmed/);
});

test('driver creation preserves its draft, ID and exact request across consent, double taps and lost-response retries', async () => {
  const app = roster(); await app.run('loadDriverList()');
  app.el('d-name').value = 'New Driver'; app.el('d-number').value = '09X';
  app.state.consent = false; await app.run('saveDriver()'); assert.equal(app.writes.length, 0);
  app.state.consent = true; app.state.lost = true;
  await Promise.all([app.run('saveDriver()'), app.run('saveDriver()')]);
  assert.equal(app.writes.length, 1); assert.equal(app.el('d-number').value, '09X');
  assert.equal(app.el('d-name').disabled, true); assert.equal(app.el('d-save').disabled, false);
  assert.match(app.el('d-feedback').textContent, /Change not confirmed/);
  app.state.lost = false; await app.run('saveDriver()');
  assert.deepEqual(app.writes[1].body, app.writes[0].body);
  assert.equal(app.drivers.filter(d => d.name === 'New Driver').length, 1);
  assert.deepEqual(Object.keys(app.writes[0].body.p_driver).sort(), ['color', 'name', 'number', 'transponder_id']);
  assert.match(app.el('d-feedback').textContent, /created — confirmed/);
});

test('driver edits preserve credits, block linked removal, retain drafts on refresh failure, and allow unused removal only with consent', async () => {
  const app = roster(); await app.run('loadDriverList()'); await app.run("editDriver('alex')");
  assert.equal(app.el('d-remove').disabled, true);
  await app.run('saveDriver(true)'); assert.equal(app.writes.length, 0);
  app.el('d-number').value = '29X'; app.run('driverFormChanged()');
  app.state.failRoster = true; await app.run('refreshDriverEditor()');
  assert.equal(app.el('d-number').value, '29X'); assert.equal(app.el('d-save').disabled, true);
  assert.match(app.el('driver-list-status').textContent, /Showing the last saved roster/);
  assert.match(app.el('driver-list').innerHTML, /Alex/);
  app.state.failRoster = false; await app.run('refreshDriverEditor()'); await app.run('saveDriver()');
  assert.equal(app.writes[0].body.p_expected.number, '19X');
  assert.equal(app.writes[0].body.p_driver.number, '29X');
  assert.equal(app.drivers[0].starts, 10); assert.equal(app.drivers[0].wins, 3);
  await app.run("editDriver('morgan')"); assert.equal(app.el('d-remove').disabled, false);
  app.state.consent = false; await app.run('saveDriver(true)'); assert.equal(app.writes.length, 1);
  app.state.consent = true; await app.run('saveDriver(true)');
  assert.equal(app.writes[1].body.p_driver, null);
  assert.equal(app.drivers.some(d => d.id === 'morgan'), false);
  assert.match(app.el('d-feedback').textContent, /removed — confirmed/);
});
