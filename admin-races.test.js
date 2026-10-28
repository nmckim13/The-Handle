const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function schedule() {
  const elements = new Map(), writes = [], receipts = new Map();
  const state = { consent: true, lost: false, readFail: false, entries: [], sessions: [], results: [], live: [] };
  const races = [
    { id: 'friday', name: 'Friday <Final>', round: 4, season: 2026, race_date: '2026-10-30', laps: 12, status: 'upcoming', winner_name: null },
    { id: 'past', name: 'Summer', round: 3, season: 2026, race_date: '2026-08-21', laps: 8, status: 'past', winner_name: 'Alex' }
  ];
  function el(id) {
    if (!elements.has(id)) {
      let value = '';
      elements.set(id, { get value() { return value; }, set value(v) { value = String(v); }, innerHTML: '', textContent: '', setAttribute() {}, disabled: false, scrollIntoView() {}, focus() {} });
    }
    return elements.get(id);
  }
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'staff' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : el(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, crypto: require('node:crypto'), URL, setTimeout, clearTimeout,
    confirm: () => state.consent,
    fetch: async (url, options = {}) => {
      const u = new URL(url), table = u.pathname.split('/').pop();
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(body), text: async () => JSON.stringify(body) });
      if (options.method) {
        assert.equal(table, 'manage_race');
        const body = JSON.parse(options.body); writes.push(body);
        if (!receipts.has(body.p_client_ref)) {
          const index = races.findIndex(r => r.id === body.p_race_id), r = races[index];
          const snapshot = r && Object.fromEntries(Object.keys(body.p_expected || body.p_race).map(k => [k,r[k]]));
          if (body.p_expected && JSON.stringify(snapshot) !== JSON.stringify(body.p_expected)) return reply({ message: 'Stale schedule; refresh and review' }, 409);
          const action = body.p_race === null ? 'removed' : body.p_expected === null ? 'created' : 'updated';
          const saved = body.p_race ? { ...r, ...body.p_race, id: body.p_race_id } : null;
          if (action === 'created') races.push(saved); else if (action === 'removed') races.splice(index,1); else races[index] = saved;
          receipts.set(body.p_client_ref, { race_id: body.p_race_id, client_ref: body.p_client_ref, action, race: saved });
        }
        return state.lost ? reply({ message: 'Reply lost after commit' }, 403) : reply(receipts.get(body.p_client_ref));
      }
      if (state.readFail) return reply({ message: 'Read unavailable' }, 403);
      return reply(table === 'races' ? races : table === 'entries' ? state.entries : table === 'race_sessions' ? state.sessions : table === 'results' ? state.results : table === 'live_state' ? state.live : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  const run = code => runInNewContext(code, scope);
  function fill(values) { for (const [key,value] of Object.entries(values)) el('r-' + key).value = value; return run('raceFormChanged()'); }
  return { el, state, races, writes, run, fill };
}

test('schedule validates whole fields, real dates and season-round uniqueness without silently defaulting laps', async () => {
  const app = schedule(); await app.run('loadRaceList();'); await app.run('newRace()');
  await app.fill({ season: 2026, round: 5, name: 'Autumn', date: '2026-11-06', laps: 10 });
  for (const [key,bad] of [['round','2nd'],['round','0'],['laps','0'],['laps','1.5'],['season','2026x'],['date','2026-02-29'],['date','2026-11-31'],['winner','Not yet']]) {
    const old = app.el('r-' + key).value; await app.fill({ [key]: bad }); await app.run('saveRace()');
    assert.equal(app.el('r-save').disabled, true, key + ': ' + bad); assert.equal(app.writes.length, 0);
    await app.fill({ [key]: old });
  }
  await app.fill({ round: 4 }); await app.run('saveRace()'); assert.match(app.el('r-validation').textContent, /already exist/);
  await app.fill({ season: 2027 });
  app.state.consent = false; await app.run('saveRace()'); assert.equal(app.writes.length, 0);
  app.state.consent = true; await app.run('saveRace()');
  assert.equal(app.writes[0].p_race.season, 2027); assert.equal(app.writes[0].p_race.round, 4); assert.equal(app.writes[0].p_race.laps, 10);
  assert.match(app.el('r-feedback').textContent, /created — confirmed/);
  assert.match(app.el('race-list').innerHTML, /Friday &lt;Final&gt;/);
});

test('reschedules retain their race and exact intent across double taps, lost replies and failed review', async () => {
  const app = schedule(); app.state.entries = [{ id: 'signup' }];
  await app.run('loadRaceList()'); await app.run("editRace('friday')");
  assert.equal(app.el('r-remove').disabled, true);
  await app.fill({ date: '2026-11-13' }); app.state.lost = true;
  await Promise.all([app.run('saveRace()'), app.run('saveRace()')]);
  assert.equal(app.writes.length, 1); assert.equal(app.writes[0].p_race_id, 'friday');
  assert.equal(app.races[0].race_date, '2026-11-13'); assert.equal(app.el('race-new').disabled, true);
  app.state.readFail = true; await app.run('refreshRaceEditor()'); assert.match(app.el('r-feedback').textContent, /pending request are kept/);
  app.state.readFail = false; app.state.lost = false; await app.run('saveRace()');
  assert.deepEqual(app.writes[1], app.writes[0]); assert.deepEqual(app.state.entries, [{ id: 'signup' }]);
  assert.match(app.el('r-feedback').textContent, /updated — confirmed/);
});

test('conflicts keep drafts for review, running races lock scheduling and only unused races offer removal', async () => {
  const app = schedule(); await app.run('loadRaceList()'); await app.run("editRace('friday')");
  await app.fill({ date: '2026-11-20' }); app.races[0].race_date = '2026-11-06';
  await app.run('saveRace()'); assert.match(app.el('r-feedback').textContent, /not confirmed/);
  await app.run('refreshRaceEditor()'); assert.equal(app.el('r-date').value, '2026-11-20'); assert.match(app.el('r-context').textContent, /Nov 6/);
  await app.run('saveRace()'); assert.equal(app.writes.at(-1).p_expected.race_date, '2026-11-06');
  app.state.sessions = [{ id: 'feature', status: 'completed' }]; app.state.results = [{ id: 'result' }];
  Object.assign(app.races[0], { status: 'past', winner_name: 'Alex', season: 2027 });
  await app.run('refreshRaceEditor()');
  for (const [key,value] of [['status','past'],['winner','Alex'],['season','2027']]) { assert.equal(app.el('r-' + key).value, value); assert.equal(app.el('r-' + key).disabled, true); }
  assert.equal(app.el('r-date').value, '2026-11-20'); assert.equal(app.el('r-remove').disabled, true);
  app.state.sessions = []; app.state.results = [];
  app.state.live = [{ id: 'broadcast', is_live: true }]; await app.run("editRace('friday')");
  assert.equal(app.el('r-save').disabled, true); assert.equal(app.el('r-date').disabled, true); assert.equal(app.el('r-remove').disabled, true);
  app.state.live = []; await app.run('refreshRaceEditor()');
  assert.equal(app.el('r-remove').disabled, false); await app.run('saveRace(true)');
  assert.equal(app.writes.at(-1).p_race, null); assert.equal(app.races.some(r => r.id === 'friday'), false);
  assert.match(app.el('r-feedback').textContent, /removed — confirmed/);
});
