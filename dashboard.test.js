const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function dashboard(data = {}) {
  const elements = new Map(), requests = [];
  const state = { missingViews: true, raceFailure: false, countFailure: false };
  const driver = { id: 'driver73', name: 'Kelton', number: '73', color: '#fff' };
  const drivers = data.drivers || [driver];
  const races = data.races || [
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
      let rows = table === 'public_drivers' ? drivers : table === 'races' ? races :
        table === 'results' ? data.results || [{ race_id: 'past', driver_id: driver.id, position: 1, points: 35, season: 2026 }] :
        table === 'public_entries' ? [{ id: 'a' }, { id: 'b' }, { id: 'c' }] : [];
      for (const field of ['id', 'driver_id', 'season']) {
        const filter = u.searchParams.get(field);
        if (filter?.startsWith('eq.')) rows = rows.filter(r => String(r[field]) === filter.slice(3));
      }
      if (table === 'results') rows = rows.map(r => ({ ...r, races: races.find(race => race.id === r.race_id) }));
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

test('standings, home and profiles drop the worst of six rounds, including missed rounds, without dropping career stats', async () => {
  const races = Array.from({ length: 6 }, (_, i) => ({ id: 'r' + i, round: i + 1, season: 2026, status: 'past', race_date: `2026-0${i + 1}-01` }));
  races.push({ id: 'canceled', season: 2026, status: 'canceled' }, { id: 'future', season: 2026, status: 'upcoming' });
  const scores = { Full: [35, 29, 26, 23, 20, 12], Missed: [32, 32, 29, 26, 20], Twice: [35, 35], Zero: [35, 32, 26, 20, 14, 0], None: [] };
  const drivers = Object.keys(scores).map(name => ({ id: name, name, number: '1' }));
  const results = Object.entries(scores).flatMap(([driver_id, points]) => points.map((points, i) => ({ race_id: 'r' + i, driver_id, points, position: i + 1, season: 2026 })));
  results.push({ race_id: 'canceled', driver_id: 'Full', points: 1000, position: 1, season: 2026 }, { race_id: 'future', driver_id: 'Full', points: 900, position: 1, season: 2026 });
  const app = dashboard({ drivers, races, results });
  app.state.missingViews = false;
  await app.run('Promise.all([loadPoints(), loadHome(), showDriverProfile("Full")])');
  const rows = [...app.el('pts-body').innerHTML.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map(m => m[1]);
  assert.match(rows[0], /Missed[\s\S]*nsc-pts">139</);
  assert.match(rows[1], /Full[\s\S]*nsc-pts">133</);
  assert.match(rows[1], /nsc-behind">−6</);
  assert.match(rows[2], /Zero[\s\S]*nsc-pts">127</);
  assert.match(rows[3], /Twice[\s\S]*nsc-pts">70</);
  assert.match(rows[4], /None[\s\S]*nsc-pts">0</);
  assert.doesNotMatch(app.el('pts-body').innerHTML, /Drop:|\(missed\)/);
  assert.match(app.el('pts-drop-rule').textContent, /Best 5 of 6 completed rounds/);
  assert.match(app.el('home-leader').innerHTML, /Missed.*139 pts/);
  assert.match(app.el('home-top5').innerHTML, /Full[\s\S]*nsc-pts">133</);
  assert.match(app.el('profile-content').innerHTML, /133<\/div><div class="prof-stat-l">Points \(after drop\)/);
  assert.match(app.el('profile-content').innerHTML, /P2<\/div><div class="prof-stat-l">Standing/);
  assert.match(app.el('profile-content').innerHTML, /2045<\/div><div class="prof-stat-l">Career Pts/);
  assert.match(rows[1], /nsc-stat">6<.*nsc-stat">1</);
  assert.ok(app.requests.every(r => r.method === 'GET'));
});

test('drop applies immediately to completed rounds only; 2025 standings remain unchanged', async () => {
  const drivers = [{ id: 'full', name: 'Full', number: '1' }];
  const races = Array.from({ length: 6 }, (_, i) => ({ id: 'r' + i, round: i + 1, season: 2026, status: i === 5 ? 'upcoming' : 'past' }));
  races.push({ id: 'old', season: 2025, status: 'past' });
  const results = [35, 29, 26, 23, 20, 12].map((points, i) => ({ driver_id: 'full', race_id: 'r' + i, points, position: i + 1, season: 2026 }));
  results.push({ driver_id: 'full', race_id: 'old', points: 32, position: 2, season: 2025 });
  const app = dashboard({ drivers, races, results });
  app.state.missingViews = false;
  await app.run('loadPoints()');
  assert.match(app.el('pts-body').innerHTML, /nsc-pts">113</);
  assert.match(app.el('pts-drop-rule').textContent, /Best 4 of 5 completed rounds/);
  await app.run('switchSeason(2025, {classList:{add(){}}})');
  await app.run('loadPoints()');
  assert.match(app.el('pts-body').innerHTML, /nsc-pts">32</);
  assert.doesNotMatch(app.el('pts-body').innerHTML, /Drop:/);
  assert.equal(app.el('pts-drop-rule').textContent, '');
});
