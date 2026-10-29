const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function dashboard() {
  const elements = new Map(), requests = [];
  const state = { missingViews: true, raceFailure: false, countFailure: false };
  const driver = { id: 'driver73', name: 'Kelton', number: '73', color: '#fff' };
  const races = [
    { id: 'past', name: 'Previous race', status: 'past', race_date: '2026-09-01', round: 2, season: 2026 },
    { id: 'next', name: 'Next race', status: 'upcoming', race_date: '2026-11-01', round: 3, season: 2026 }
  ];
  function el(id) {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', value: '', style: {},
      classList: { add() {}, remove() {}, contains: () => false }, querySelector: () => el(id + '-child') });
    return elements.get(id);
  }
  const scope = {
    window: { addEventListener() {}, LiveBoard: { create: () => ({}) } },
    document: { hidden: false, getElementById: el, querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, location: { hash: '' }, history: { replaceState() {} },
    URL, AbortController, setTimeout, clearTimeout, setInterval() {},
    fetch: async (url, options = {}) => {
      const u = new URL(url), table = u.pathname.split('/').pop();
      requests.push({ table, method: options.method || 'GET' });
      const failed = state.raceFailure && table === 'races' ||
        (state.missingViews && (table === 'public_drivers' || table === 'public_entries' ||
          u.searchParams.get('select')?.includes('public_drivers'))) ||
        state.countFailure && table === 'public_entries';
      const rows = table === 'public_drivers' ? [driver] : table === 'races' ? races :
        table === 'results' ? [{ race_id: 'past', driver_id: driver.id, position: 1, points: 35 }] :
        table === 'public_entries' ? [{ id: 'a' }, { id: 'b' }, { id: 'c' }] : [];
      return { ok: !failed, status: failed ? 404 : 200,
        text: async () => 'Missing public database contract', json: async () => structuredClone(rows) };
    }
  };
  for (const file of ['race-night-client.js', 'driver-graphics.js']) {
    runInNewContext(readFileSync(`${__dirname}/${file}`, 'utf8'), scope);
  }
  const html = readFileSync(`${__dirname}/index.html`, 'utf8');
  const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1];
  runInNewContext(script, scope);
  return { el, requests, state, run: code => runInNewContext(code, scope) };
}

test('missing safe views leave available races visible and replace rejected public loaders without legacy reads', async () => {
  const app = dashboard();
  await app.run('Promise.all([loadHome(), loadDrivers(), loadPoints(), loadRecords(), showDriverProfile("driver73"), loadSignupDrivers()])');
  assert.equal(app.el('sc-drivers').textContent, '—');
  assert.equal(app.el('sc-races').textContent, 1);
  assert.match(app.el('home-nextrace').innerHTML, /Round 3/);
  assert.match(app.el('home-nextrace').innerHTML, /Sign-up count unavailable/);
  assert.doesNotMatch(app.el('home-nextrace').innerHTML, /Be the first/);
  for (const id of ['home-winner', 'driver-grid', 'pts-body', 'rec-body', 'club-13sec', 'profile-content', 'signup-msg']) {
    assert.match(app.el(id).innerHTML, /temporarily unavailable/);
    assert.doesNotMatch(app.el(id).innerHTML, /class="loading"/);
  }
  assert.match(app.el('su-driver').innerHTML, /temporarily unavailable/);
  assert.ok(app.requests.every(r => r.method === 'GET' && !['drivers', 'entries'].includes(r.table)));

  app.state.missingViews = false;
  await app.run('Promise.all([loadHome(), loadDrivers(), loadPoints(), loadRecords()])');
  assert.equal(app.el('sc-drivers').textContent, 1);
  assert.match(app.el('home-winner').innerHTML, /Kelton/);
  assert.match(app.el('home-nextrace').innerHTML, /<b>3<\/b> drivers/);
  assert.match(app.el('driver-grid').innerHTML, /kelton73.png/);
  assert.match(app.el('pts-body').innerHTML, /Kelton/);
  assert.doesNotMatch(app.el('rec-body').innerHTML, /temporarily unavailable/);
});

test('race failures do not become empty schedules or zero counts, and count-only failure keeps winner and standings', async () => {
  const app = dashboard();
  await app.run('Promise.all([loadHome(), loadDrivers(), loadPoints(), loadSchedule(), loadRecords(), loadVideos(), loadLatestVideo()])');
  app.state.missingViews = false;
  app.state.raceFailure = true;
  await app.run('loadHome()');
  assert.equal(app.el('sc-drivers').textContent, 1);
  assert.equal(app.el('sc-races').textContent, '—');
  assert.match(app.el('home-nextrace').innerHTML, /Schedule temporarily unavailable/);
  assert.doesNotMatch(app.el('home-nextrace').innerHTML, /Schedule TBD/);
  app.state.raceFailure = false;
  app.state.countFailure = true;
  await app.run('loadHome()');
  assert.equal(app.el('sc-races').textContent, 1);
  assert.match(app.el('home-winner').innerHTML, /Kelton/);
  assert.match(app.el('home-top5').innerHTML, /Kelton/);
  assert.match(app.el('home-nextrace').innerHTML, /Round 3/);
  assert.match(app.el('home-nextrace').innerHTML, /Sign-up count unavailable/);
});
