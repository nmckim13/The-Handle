const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function board(load) {
  let now = Date.parse('2026-10-30T23:00:00Z');
  const fields = Object.fromEntries(['[data-board-headline]', '[data-board-checked]', '[data-board-timing]', 'button'].map(key => [key, {
    textContent: '', dataset: {}, addEventListener(event, callback) { this[event] = callback; }
  }]));
  const root = { classList: { add() {} }, dataset: {}, querySelector: key => fields[key] };
  const intervals = new Map();
  const timeouts = new Map();
  const events = {};
  const window = { navigator: { onLine: true }, addEventListener: (event, callback) => { events[event] = callback; } };
  const document = { hidden: false, addEventListener: (event, callback) => { events[event] = callback; } };
  runInNewContext(readFileSync(`${__dirname}/live-board.js`, 'utf8'), {
    window, document, AbortController,
    Date: class extends Date { static now() { return now; } },
    setInterval: (callback, ms) => intervals.set(ms, callback),
    setTimeout: (callback, ms) => { timeouts.set(callback, ms); return callback; },
    clearTimeout: callback => timeouts.delete(callback)
  });
  const updates = window.LiveBoard.create({ root, load });
  return {
    updates, root, window, document, events,
    headline: () => fields['[data-board-headline]'].textContent,
    checked: () => fields['[data-board-checked]'].textContent,
    timing: () => fields['[data-board-timing]'].textContent,
    button: fields.button,
    advance(ms) { now += ms; intervals.get(1000)(); },
    async poll() { intervals.get(15000)(); await new Promise(setImmediate); },
    timeout: () => { for (const callback of timeouts.keys()) callback(); }
  };
}

test('board freshness and source timing age advance independently at their boundaries', async () => {
  const b = board(async () => ({ live: true, timingAt: '2026-10-30T22:59:01Z' }));
  await b.updates.refresh();
  b.updates.setRealtime('SUBSCRIBED');
  assert.equal(b.headline(), 'Board up to date');
  assert.equal(b.timing(), 'Timing received 59s ago');
  b.advance(1000);
  assert.equal(b.timing(), 'No recent timing · last received 1m 0s ago');
  assert.equal(b.root.dataset.state, 'current');
  b.advance(43999);
  assert.equal(b.root.dataset.state, 'current');
  b.advance(1);
  assert.equal(b.headline(), 'Board may be out of date');
  // A connected socket is not proof that the board's last snapshot is current.
  assert.match(b.checked(), /45s ago.*Live updates/);
  await b.updates.refresh();
  assert.equal(b.headline(), 'Board up to date');
  assert.equal(b.timing(), 'No recent timing · last received 1m 44s ago');
});

test('failed updates keep the last successful age, recover, and do not turn an outage into no-race', async () => {
  let result = { live: true, timingAt: '2026-10-30T23:00:00Z' };
  const b = board(async () => { if (result instanceof Error) throw result; return result; });
  await b.updates.refresh();
  b.advance(8000);
  result = new Error('Disconnected');
  await b.button.click();
  assert.equal(b.headline(), 'Update failed — showing last good board');
  assert.match(b.checked(), /8s ago/);
  assert.equal(b.button.disabled, false);
  result = { live: false };
  await b.poll();
  assert.equal(b.headline(), 'No race live right now');
  assert.equal(b.timing(), 'Timing feed waits for race night');
  result = new Error('Initial failure');
  const first = board(async () => { throw result; });
  await first.updates.refresh();
  assert.equal(first.headline(), 'Could not load board — retrying automatically');
  assert.doesNotMatch(first.checked(), /Board checked/);
});

test('offline pauses requests and reconnect or returning to the tab refreshes automatically', async () => {
  let received = false;
  const b = board(async () => { received = true; return { live: true }; });
  b.window.navigator.onLine = false;
  b.events.offline();
  await b.updates.refresh();
  assert.equal(received, false);
  assert.equal(b.headline(), 'Offline — updates paused');
  assert.equal(b.button.disabled, true);
  b.window.navigator.onLine = true;
  b.events.online();
  await new Promise(setImmediate);
  assert.equal(b.headline(), 'Board up to date');
  assert.equal(b.timing(), 'No timing received for this race');
  received = false;
  b.document.hidden = true;
  await b.poll();
  assert.equal(received, false);
  b.document.hidden = false;
  b.events.visibilitychange();
  await new Promise(setImmediate);
  assert.equal(received, true);
  b.updates.setRealtime('CHANNEL_ERROR');
  assert.match(b.checked(), /15s auto-refresh/);
  assert.doesNotMatch(b.checked(), /Live updates/);
});

test('overlapping refreshes share a snapshot and an aborted slow refresh can recover', async () => {
  let slow = true;
  const b = board(signal => slow
    ? new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason)))
    : Promise.resolve({ live: true, timingUnavailable: true }));
  const pending = b.updates.refresh();
  assert.equal(b.updates.refresh(), pending);
  assert.equal(b.button.disabled, true);
  await Promise.resolve();
  b.timeout();
  await pending;
  assert.equal(b.root.dataset.state, 'error');
  assert.equal(b.button.disabled, false);
  slow = false;
  await b.updates.refresh();
  assert.equal(b.headline(), 'Board up to date');
  assert.equal(b.timing(), 'Timing feed unavailable · race board still available');
});

test('invalid or future timing timestamps never claim a fresh feed', async () => {
  let timingAt = 'not a timestamp';
  const b = board(async () => ({ live: true, timingAt }));
  await b.updates.refresh();
  assert.equal(b.timing(), 'No timing received for this race');
  timingAt = '2026-10-30T23:00:06Z';
  await b.updates.refresh();
  assert.equal(b.timing(), 'Timing timestamp is ahead of this device’s clock');
});
