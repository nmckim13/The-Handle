const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function editor() {
  const elements = new Map(), writes = [], receipts = new Map();
  const state = { lost: false, readFail: false, consent: true };
  const rows = [{ id: 'warning', message: 'Rain delay', type: 'Rain Delay', active: true, created_at: '2026-10-20T12:00:00+00:00', updated_at: '2026-10-20T12:00:00+00:00' }];
  function el(id) {
    if (!elements.has(id)) elements.set(id, { value: '', textContent: '', innerHTML: '', setAttribute() {}, scrollIntoView() {}, focus() {} });
    return elements.get(id);
  }
  const snapshot = a => ({ message: a.message, type: a.type, active: a.active, created_at: a.created_at, updated_at: a.updated_at });
  const active = () => rows.filter(a => a.active).map(a => ({ id: a.id, ...snapshot(a) })).sort((a,b) => a.id.localeCompare(b.id));
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'staff' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : el(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, crypto: require('node:crypto'), URL, setTimeout, clearTimeout,
    confirm: () => state.consent,
    fetch: async (url, options = {}) => {
      const u = new URL(url), table = u.pathname.split('/').pop();
      const reply = (body,status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(body), text: async () => JSON.stringify(body) });
      if (options.method) {
        assert.equal(table,'manage_announcement'); const body = JSON.parse(options.body); writes.push(body);
        if (!receipts.has(body.p_client_ref)) {
          const old = rows.find(a => a.id === body.p_announcement_id), proposed = body.p_announcement;
          if (body.p_expected && (!old || JSON.stringify(snapshot(old)) !== JSON.stringify(body.p_expected))) return reply({ message: 'Stale notice' },409);
          if ((proposed?.active || body.p_announcement_id === null) && JSON.stringify(active()) !== JSON.stringify(body.p_active_expected)) return reply({ message: 'Active notices changed' },409);
          const action = body.p_announcement_id === null ? 'cleared' : proposed === null ? 'removed' : body.p_expected === null ? 'created' : 'updated';
          if (proposed?.active || action === 'cleared') rows.forEach(a => { a.active = false; });
          let saved = null;
          if (proposed) {
            saved = { ...old, ...proposed, id: body.p_announcement_id, created_at: old?.created_at || '2026-10-28T12:00:00+00:00', updated_at: '2026-10-28T12:01:00+00:00' };
            if (old) Object.assign(old,saved); else rows.push(saved);
          } else if (old) rows.splice(rows.indexOf(old),1);
          receipts.set(body.p_client_ref,{ announcement_id: body.p_announcement_id, client_ref: body.p_client_ref, action, announcement: saved });
        }
        return state.lost ? reply({ message: 'Reply lost' },403) : reply(receipts.get(body.p_client_ref));
      }
      if (state.readFail) return reply({ message: 'Read unavailable' },403);
      return reply(table === 'announcements' ? rows.filter(a => (!u.searchParams.has('active') || a.active) && (!u.searchParams.has('id') || 'eq.' + a.id === u.searchParams.get('id'))) : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`,'utf8'),scope);
  const html = readFileSync(`${__dirname}/admin/index.html`,'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1],scope);
  return { el, rows, writes, state, run: code => runInNewContext(code,scope) };
}

test('announcement templates are editable drafts, validate text and require consent before publishing', async () => {
  const app = editor(); await app.run('loadAnnouncements()'); await app.run('allClearAnnounce()');
  assert.equal(app.writes.length,0); assert.equal(app.rows[0].active,true); assert.equal(app.el('ann-type').value,'All Clear');
  app.el('ann-msg').value = ' '; await app.run('saveAnnouncement()'); assert.equal(app.el('ann-save').disabled,true);
  app.el('ann-msg').value = 'x'.repeat(1001); await app.run('saveAnnouncement()'); assert.equal(app.writes.length,0);
  app.el('ann-msg').value = '<img src=x onerror=run()> Race resumes at 7.'; await app.run('announcementChanged()');
  assert.equal(app.el('ann-preview-message').textContent,app.el('ann-msg').value);
  app.state.consent = false; await app.run('saveAnnouncement()'); assert.equal(app.writes.length,0);
  app.state.consent = true; await app.run('saveAnnouncement()');
  assert.equal(app.rows.filter(a => a.active).length,1); assert.equal(app.rows[0].active,false);
  assert.match(app.el('ann-list').innerHTML,/&lt;img/); assert.match(app.el('ann-feedback').textContent,/created — confirmed/);
});

test('lost announcement replies keep one immutable publication intent and failed review cannot erase it', async () => {
  const app = editor(); await app.run('loadAnnouncements()'); await app.run("quickAnnounce('Reschedule','Race moved to Friday, November 6.')");
  app.state.lost = true; await Promise.all([app.run('saveAnnouncement()'),app.run('saveAnnouncement()')]);
  assert.equal(app.writes.length,1); assert.equal(app.rows.length,2); assert.equal(app.el('ann-msg').disabled,true);
  app.state.readFail = true; await app.run('reviewAnnouncement()'); assert.match(app.el('ann-feedback').textContent,/pending request are kept/);
  app.state.readFail = false; app.state.lost = false; await app.run('saveAnnouncement()');
  assert.deepEqual(app.writes[1],app.writes[0]); assert.equal(app.rows.length,2); assert.equal(app.rows.filter(a => a.active).length,1);
});

test('a new warning blocks stale all-clear until review; hide-all preserves drafts and hidden edits preserve other banners', async () => {
  const app = editor(); await app.run('loadAnnouncements()'); await app.run('allClearAnnounce()');
  app.rows.push({ ...app.rows[0], id: 'new-warning', message: 'Race cancelled', type: 'Cancellation', updated_at: '2026-10-28T12:01:00+00:00' });
  await app.run('saveAnnouncement()'); assert.match(app.el('ann-feedback').textContent,/not confirmed/);
  await app.run('reviewAnnouncement()'); assert.match(app.el('ann-impact').textContent,/Race cancelled/);
  assert.equal(app.el('ann-type').value,'All Clear'); await app.run('saveAnnouncement()');
  await app.run('newAnnouncement()'); app.el('ann-msg').value = 'Unfinished draft'; await app.run('announcementChanged()');
  await app.run("saveAnnouncement('clear')"); assert.equal(app.rows.some(a => a.active),false); assert.equal(app.el('ann-msg').value,'Unfinished draft');
  await app.run('reviewAnnouncement()'); await app.run('saveAnnouncement()'); assert.equal(app.rows.at(-1).message,'Unfinished draft'); assert.equal(app.rows.at(-1).active,false);
  const id = app.rows.at(-1).id; await app.run('reviewAnnouncement()'); await app.run("saveAnnouncement('remove')"); assert.equal(app.rows.some(a => a.id === id),false);
});

test('public banners refresh, dismiss only one revision, retain warnings on failure, and remove deactivated notices', async () => {
  const time = {}, banner = { className: '', innerHTML: '', querySelector: () => time }, stored = new Map();
  let rows = [{ id: 'a',message: '<img onerror=run()>',type: 'Rain Delay',updated_at: '2026-10-28T12:00:00Z' }], fail = false, reads = 0;
  const scope = { window: { addEventListener() {} }, document: { hidden: false, getElementById: () => banner, querySelector: () => time, addEventListener() {} }, navigator: { onLine: true },
    URL, AbortController, setTimeout, clearTimeout, setInterval() {}, sessionStorage: { getItem: key => stored.get(key), setItem: (key,value) => stored.set(key,value) },
    get: async () => { reads++; if (fail) throw new Error('Unavailable'); return structuredClone(rows); } };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`,'utf8'),scope);
  Object.assign(scope,scope.window.RaceNight);
  const html = readFileSync(`${__dirname}/index.html`,'utf8');
  const script = html.slice(html.indexOf('// ANNOUNCEMENTS\nlet announcementRead'),html.indexOf('loadHome(); loadDrivers();'));
  runInNewContext(script,scope); const run = code => runInNewContext(code,scope); await run('loadAnnouncement()');
  assert.match(banner.innerHTML,/&lt;img/); assert.equal(banner.className,'show ann-rain_delay');
  await run('dismissAnnouncement();loadAnnouncement()'); assert.equal(banner.innerHTML,'');
  rows[0].updated_at = '2026-10-28T12:01:00Z'; rows[0].message = 'New update'; await run('loadAnnouncement()'); assert.match(banner.innerHTML,/New update/);
  fail = true; await run('loadAnnouncement()'); assert.match(banner.innerHTML,/New update/); assert.match(time.textContent,/last message kept/);
  fail = false; rows = []; await run('loadAnnouncement()'); assert.equal(banner.className,'');
  scope.navigator.onLine = false; const before = reads; await run('loadAnnouncement()'); assert.equal(reads,before);
});
