const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

test('walk-up re-taps and lost-response retries create one entry and retain the draft until confirmed', async () => {
  const elements = new Map();
  for (const id of ['rn-body', 'rn-checkin', 'rn-checkin-feedback', 'rn-race-select', 'rn-walkup', 'rn-walkup-name', 'rn-walkup-number', 'rn-walkup-color']) {
    elements.set(id, { value: '', innerHTML: '', setAttribute() {}, contains: () => false });
  }
  const button = { textContent: 'Add & Check In', disabled: false };
  const entries = [];
  let responseLost = true;
  let nextRef = 0;
  const scope = {
    window: {
      addEventListener() {},
      AdminAuth: { create: () => ({ getAccessToken: async () => 'staff-test-token' }) },
      DriverGraphics: { numHtml: () => '19X' }
    },
    document: {
      getElementById: id => elements.get(id),
      querySelectorAll: selector => selector === '.rn-session' ? [] : [button],
      addEventListener() {}
    },
    navigator: { onLine: true },
    localStorage: { getItem: () => null },
    crypto: { randomUUID: () => `receipt-${++nextRef}` },
    URL, setTimeout: callback => callback(), clearTimeout() {}, confirm: () => true,
    fetch: async (url, options = {}) => {
      const table = new URL(url).pathname.split('/').pop();
      const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data, text: async () => JSON.stringify(data) });
      if (options.method === 'POST') {
        const entry = JSON.parse(options.body);
        const existing = entries.find(e => e.client_ref === entry.client_ref);
        if (!existing) entries.push({ ...entry, id: 'entry-' + entries.length });
        // The insert landed, but none of its retry responses arrived.
        if (responseLost) return reply('Response unavailable', 503);
        if (existing) return reply('duplicate key client_ref', 409);
        return reply([entry]);
      }
      if (table === 'races') return reply([{ id: 'race-one', name: 'Race', race_date: '2026-10-30' }]);
      return reply(table === 'entries' ? entries.slice() : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  const script = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(match => match[1].trim())[1];
  runInNewContext(script, scope);
  scope.button = button;
  runInNewContext("RN.raceId = 'race-one'", scope);
  await runInNewContext('rnRenderAll()', scope);
  elements.get('rn-walkup-name').value = 'Taylor Draft';
  elements.get('rn-walkup-number').value = '19X';
  elements.get('rn-walkup-color').value = '#F26419';

  await Promise.all([
    runInNewContext('rnAddWalkup(button)', scope),
    runInNewContext('rnAddWalkup(button)', scope)
  ]);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].status, 'checked_in');
  assert.equal(elements.get('rn-walkup-name').value, 'Taylor Draft');
  assert.equal(elements.get('rn-walkup-number').value, '19X');
  assert.match(elements.get('rn-checkin-feedback').textContent, /Could not confirm/);
  assert.equal(button.disabled, false);

  responseLost = false;
  await runInNewContext('rnAddWalkup(button)', scope);
  assert.equal(entries.length, 1);
  assert.equal(elements.get('rn-walkup-name').value, '');
  assert.equal(elements.get('rn-walkup-number').value, '');
  assert.match(elements.get('rn-checkin-feedback').textContent, /Taylor Draft checked in/);
});

test('bulk check-in retries only the confirmed roster and preserves concurrent withdrawals and new signups', async () => {
  const elements = new Map();
  for (const id of ['rn-body', 'rn-checkin', 'rn-checkin-feedback', 'rn-race-select']) {
    elements.set(id, { value: '', innerHTML: '', setAttribute() {}, contains: () => false });
  }
  const button = { textContent: 'Check In All', disabled: false };
  const entry = (id, race_id = 'race-one') => ({ id, race_id, name: id, car_number: id, status: 'registered' });
  const entries = [entry('reviewed'), entry('withdrawing'), entry('other-race', 'race-two')];
  let patchAttempts = 0;
  const scope = {
    window: {
      addEventListener() {},
      AdminAuth: { create: () => ({ getAccessToken: async () => 'staff-test-token' }) },
      DriverGraphics: { numHtml: () => '7' }
    },
    document: {
      getElementById: id => elements.get(id),
      querySelectorAll: selector => selector === '.rn-session' ? [] : [button],
      addEventListener() {}
    },
    navigator: { onLine: true }, localStorage: { getItem: () => null },
    URL, setTimeout: callback => callback(), clearTimeout() {},
    confirm: message => {
      assert.match(message, /ALL 2 waiting drivers/);
      entries.find(e => e.id === 'withdrawing').status = 'withdrawn';
      entries.push(entry('signup-before-save'));
      return true;
    },
    fetch: async (url, options = {}) => {
      const u = new URL(url), table = u.pathname.split('/').pop(), query = u.searchParams;
      const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(data), text: async () => JSON.stringify(data) });
      if (options.method === 'PATCH') {
        patchAttempts++;
        const ids = query.get('id')?.slice(4, -1).split(',');
        for (const e of entries) {
          if ((!ids || ids.includes(e.id)) && (!query.has('race_id') || query.get('race_id') === 'eq.' + e.race_id)
            && (!query.has('status') || query.get('status') === 'eq.' + e.status)) Object.assign(e, JSON.parse(options.body));
        }
        if (patchAttempts === 1) {
          // The update committed, but its reply was lost. A new online signup
          // arrives before the shared client's automatic PATCH retry.
          entries.push(entry('signup-before-retry'));
          throw new Error('Response lost after commit');
        }
        return reply(null);
      }
      if (table === 'races') return reply([{ id: 'race-one', name: 'Race', race_date: '2026-10-30' }]);
      return reply(table === 'entries' ? entries.filter(e => query.get('race_id') === 'eq.' + e.race_id) : []);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  scope.button = button;
  runInNewContext("RN.raceId = 'race-one'", scope);

  await runInNewContext('rnCheckInAll(button)', scope);

  assert.equal(patchAttempts, 2);
  assert.deepEqual(Object.fromEntries(entries.map(e => [e.id, e.status])), {
    reviewed: 'checked_in', withdrawing: 'withdrawn', 'other-race': 'registered',
    'signup-before-save': 'registered', 'signup-before-retry': 'registered'
  });
  assert.equal(button.disabled, false);
  assert.doesNotMatch(elements.get('rn-checkin-feedback').textContent, /Could not confirm/);
});
