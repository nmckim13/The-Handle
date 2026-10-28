const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function profiles() {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) {
      let value = '';
      elements.set(id, { get value() { return value; }, set value(next) { value = String(next); }, innerHTML: '', setAttribute() {}, style: {}, disabled: false });
    }
    return elements.get(id);
  };
  const state = { consent: true, lost: false, fail: false };
  const driver = { id: 'alex', name: 'Alex', number: '07', age: 0, nickname: 'Ice', hometown: 'Portage', bio: 'Original story', starts: 12, wins: 3, birthday: '1999-05-01', transponder_id: 0 };
  const writes = [], receipts = new Map();
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'staff' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : el(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, crypto: require('node:crypto'), URL, setTimeout, clearTimeout,
    confirm: () => state.consent,
    fetch: async (url, options = {}) => {
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(body)), text: async () => JSON.stringify(body) });
      if (!options.method) return state.fail ? reply({ message: 'Unavailable' }, 403) : reply([driver]);
      assert.equal(new URL(url).pathname.split('/').pop(), 'save_driver_profile');
      const body = JSON.parse(options.body); writes.push(body);
      if (!receipts.has(body.p_client_ref)) {
        if (Object.keys(body.p_expected).some(k => (driver[k] ?? null) !== body.p_expected[k])) return reply({ message: 'Profile changed; refresh and review' }, 409);
        Object.assign(driver, body.p_profile);
        receipts.set(body.p_client_ref, { driver_id: driver.id, client_ref: body.p_client_ref, profile: body.p_profile });
      }
      return state.lost ? reply({ message: 'Confirmation lost' }, 403) : reply(receipts.get(body.p_client_ref));
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  return { el, state, driver, writes, run: code => runInNewContext(code, scope) };
}

test('profiles validate optional age and photo links, escape preview, and preserve draft on navigation and failed reads', async () => {
  const app = profiles(); await app.run('loadProfileDriverSelect()');
  app.el('prof-driver').value = 'alex'; await app.run('loadProfileForm()');
  assert.equal(app.el('pf-age').value, '0');
  app.el('pf-age').value = '34.5'; await app.run('saveProfile()');
  assert.match(app.el('profile-validation').textContent, /whole number/);
  app.el('pf-age').value = '121'; await app.run('saveProfile()');
  app.el('pf-age').value = ''; app.el('pf-photo_url').value = 'javascript:alert(1)'; await app.run('saveProfile()');
  assert.equal(app.writes.length, 0);
  app.el('pf-photo_url').value = ''; app.el('pf-bio').value = '<img src=x onerror=alert(1)>\nNew story';
  app.run('profileChanged()');
  assert.match(app.el('profile-preview').innerHTML, /&lt;img/);
  assert.doesNotMatch(app.el('profile-preview').innerHTML, /<img/);
  app.state.consent = false; app.el('prof-driver').value = ''; await app.run('loadProfileForm()');
  assert.equal(app.el('prof-driver').value, 'alex');
  app.state.fail = true; await app.run('refreshProfile()'); await app.run('loadProfileDriverSelect()');
  assert.match(app.el('prof-roster-status').textContent, /previous list kept/);
  assert.match(app.el('pf-bio').value, /New story/);
  app.state.fail = false; app.state.consent = true; await app.run('saveProfile()');
  assert.equal(app.writes[0].p_profile.age, null);
  assert.equal(app.driver.birthday, '1999-05-01'); assert.equal(app.driver.starts, 12); assert.equal(app.driver.transponder_id, 0);
  assert.match(app.el('profile-status').textContent, /saved — confirmed/);
});

test('profile saves replay the original intent after lost replies and require review after conflicting staff edits', async () => {
  const app = profiles(); await app.run('loadProfileDriverSelect()');
  app.el('prof-driver').value = 'alex'; await app.run('loadProfileForm()');
  app.el('pf-hometown').value = ''; app.el('pf-nickname').value = 'New nickname'; app.state.lost = true;
  await Promise.all([app.run('saveProfile()'), app.run('saveProfile()')]);
  assert.equal(app.writes.length, 1); assert.equal(app.el('pf-bio').disabled, true);
  app.state.lost = false; await app.run('saveProfile()');
  assert.deepEqual(app.writes[1], app.writes[0]); assert.equal(app.driver.hometown, null);
  app.el('pf-bio').value = 'My draft'; app.driver.bio = 'Another staff edit';
  await app.run('saveProfile()');
  assert.equal(app.driver.bio, 'Another staff edit'); assert.match(app.el('profile-status').textContent, /not confirmed/);
  await app.run('refreshProfile()');
  assert.equal(app.el('pf-bio').value, 'My draft');
  assert.match(app.el('profile-saved-values').textContent, /Another staff edit/);
  assert.equal(app.el('pf-bio').disabled, false);
  await app.run('saveProfile()');
  assert.equal(app.writes.at(-1).p_expected.bio, 'Another staff edit');
  assert.equal(app.driver.bio, 'My draft'); assert.equal(app.driver.wins, 3);
});
