const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function qualifyingPage() {
  const storage = new Map(), timers = new Map(), writes = [];
  let timerId = 0;
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'staff-test-token' }) } },
    document: { getElementById: () => null, querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, URL,
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options = {}) => {
      if (options.method === 'POST') writes.push(JSON.parse(options.body));
      return { ok: true, json: async () => [] };
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(match => match[1].trim())[1];
  runInNewContext(script, scope);
  runInNewContext("RN.raceId = 'friday'", scope);
  return {
    storage, writes,
    run: code => runInNewContext(code, scope),
    async flush() { for (const fn of timers.values()) await fn(); timers.clear(); }
  };
}

test('qualifying saves positive decimal seconds, not partial, negative, zero, or non-finite times', () => {
  const page = qualifyingPage();
  for (const [input, expected] of [['12.345', 12.345], [' 9,876 ', 9.876], ['.125', .125], ['1', 1], ['12seconds', null], ['0', null], ['-3', null], ['Infinity', null], ['1e2', null], ['9.8765', null], ['', null]]) {
    page.run(`rnSetQual('driver', ${JSON.stringify(input)})`);
    const times = JSON.parse(page.storage.get('rn_qual_friday'));
    assert.equal(times.driver ?? null, expected, input);
  }
});

test('a delayed qualifying mirror stays with its original race after the operator switches races', async () => {
  const page = qualifyingPage();
  page.run("rnSetQual('alex', '12.345'); RN.raceId = 'saturday';");
  await page.flush();
  assert.equal(page.writes.length, 1);
  assert.equal(page.writes[0].race_id, 'friday');
  assert.deepEqual(page.writes[0].qual_times, { alex: 12.345 });
  assert.deepEqual(JSON.parse(page.storage.get('rn_qual_friday')), { alex: 12.345 });
  assert.equal(page.storage.has('rn_qual_saturday'), false);
});

test('qualifying keeps the chosen display and unsaved lap when refreshing the same race, not a different race', () => {
  const page = qualifyingPage();
  page.run(`
    document.getElementById = id => ({
      'rn-qualifying': { dataset: { raceId: 'friday' } },
      'rn-qual-compact': { checked: false },
      'rn-qual-time-alex': { value: '12.', getAttribute: () => 'true' }
    })[id] || null;
  `);
  const render = () => page.run("rnQualifyingCard([{ id: 'alex', name: 'Alex', car_number: '18' }])");
  const refreshed = render();
  assert.match(refreshed, /id="rn-qual-compact" type="checkbox">/);
  assert.match(refreshed, /value="12\."[^>]*aria-invalid="true"/);
  page.run("RN.raceId = 'saturday'");
  const differentRace = render();
  assert.match(differentRace, /id="rn-qual-compact" type="checkbox" checked>/);
  assert.doesNotMatch(differentRace, /value="12\."/);
  assert.equal(page.writes.length, 0);
});

test('name-based time editing works without qualifying fields, rejects invalid edits, and cannot save into a different race', () => {
  const page = qualifyingPage();
  page.run(`
    const editor = { dataset: {}, open: false, showModal() { this.open = true; }, close() { this.open = false; } };
    const timeField = { value: '', setAttribute() {}, focus() {}, select() {} };
    const message = { textContent: '' };
    const driver = { querySelector: () => ({ textContent: '#18 Alex' }) };
    document.getElementById = id => ({
      'rn-qual-edit': editor, 'rn-qual-edit-time': timeField,
      'rn-qual-edit-error': message, 'rn-qual-edit-driver': { textContent: '' }
    })[id] || null;
    rnSetQual('alex', '13.456');
    rnEditQual('alex', driver);
  `);
  assert.equal(Number(page.run('timeField.value')), 13.456);
  page.run("timeField.value = '12seconds'; rnSaveQualEdit()");
  assert.deepEqual(JSON.parse(page.storage.get('rn_qual_friday')), { alex: 13.456 });
  assert.equal(page.run('editor.open'), true);
  assert.match(page.run('message.textContent'), /positive seconds/);
  page.run("timeField.value = '11,876'; rnSaveQualEdit()");
  assert.deepEqual(JSON.parse(page.storage.get('rn_qual_friday')), { alex: 11.876 });
  assert.equal(page.run('editor.open'), false);
  page.run("rnEditQual('alex', driver); timeField.value = '9.125'; RN.raceId = 'saturday'; rnSaveQualEdit()");
  assert.equal(page.storage.has('rn_qual_saturday'), false);
  assert.deepEqual(JSON.parse(page.storage.get('rn_qual_friday')), { alex: 11.876 });
  page.run("RN.raceId = 'friday'; rnEditQual('alex', driver); timeField.value = ''; rnSaveQualEdit()");
  assert.deepEqual(JSON.parse(page.storage.get('rn_qual_friday')), {});
});
