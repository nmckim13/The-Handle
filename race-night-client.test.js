const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const window = {};
runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), { window, URL });

test('race countdown uses track calendar days, including midnight and daylight-saving boundaries', () => {
  const cases = [
    ['2026-10-30', '2026-10-27T17:00:00Z', '3 days away'],
    ['2026-10-30', '2026-10-30T04:59:59Z', '1 day away'],
    ['2026-10-30', '2026-10-30T05:00:00Z', 'Race day!'],
    ['2026-10-30', '2026-10-31T04:59:59Z', 'Race day!'],
    ['2026-10-30', '2026-10-31T05:00:00Z', 'past due'],
    ['2026-03-09', '2026-03-08T07:59:59Z', '1 day away'],
    ['2026-03-09', '2026-03-08T08:00:00Z', '1 day away'],
    ['2026-11-02', '2026-11-01T06:59:59Z', '1 day away'],
    ['2026-11-02', '2026-11-01T07:00:00Z', '1 day away'],
    ['2027-01-01', '2026-12-31T18:00:00Z', '1 day away']
  ];
  for (const [raceDate, now, expected] of cases) {
    assert.equal(window.RaceNight.raceCountdown(raceDate, new Date(now)), expected, `${raceDate} at ${now}`);
  }
});

test('HTML, URLs and colors reject executable content without changing ordinary text', () => {
  assert.equal(window.RaceNight.escapeHtml('<img onerror="run()"> & \'text\''), '&lt;img onerror=&quot;run()&quot;&gt; &amp; &#39;text&#39;');
  assert.equal(window.RaceNight.safeUrl('javascript:alert(1)'), '');
  assert.equal(window.RaceNight.safeUrl('data:text/html,<script>run()</script>'), '');
  assert.equal(window.RaceNight.safeUrl('/logo.png'), 'https://yardkartz.com/logo.png');
  assert.equal(window.RaceNight.safeUrl('https://example.com/photo.jpg'), 'https://example.com/photo.jpg');
  assert.equal(window.RaceNight.safeColor('#F26419'), '#F26419');
  assert.equal(window.RaceNight.safeColor('#fff'), '#fff');
  assert.equal(window.RaceNight.safeColor('red;" onmouseover="run()'), '#fff');
});

test('announcement age rejects unknown times and advances at minute, hour and day boundaries', () => {
  const now = Date.parse('2026-10-28T12:00:00Z'), age = window.RaceNight.announcementAge;
  for (const value of [null, '', 'invalid', '2026-10-28T12:06:00Z']) assert.equal(age(value,now),'time unavailable');
  for (const [seconds,label] of [[-1,'just now'],[119,'just now'],[120,'2m ago'],[3599,'59m ago'],[3600,'1h ago'],[86400,'1d ago']]) assert.equal(age(new Date(now - seconds * 1000).toISOString(),now),label);
});

test('REST operations use the current staff token and cannot fall back to anonymous writes', async () => {
  const scope = { window: {}, URL };
  const requests = [];
  scope.fetch = async (_url, options) => {
    requests.push(options);
    return { ok: true, json: async () => [] };
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  let token = 'first-session';
  const client = scope.window.RaceNight.createClient('https://example.com', 'public-key', {
    getAccessToken: async () => {
      if (!token) { const error = new Error('Signed out'); error.httpStatus = 401; throw error; }
      return token;
    }
  });
  await client.get('entries');
  token = 'refreshed-session';
  await client.post('entries', { name: 'Example' });
  await client.patch('entries', 'id=eq.example', { status: 'checked_in' });
  await client.del('entries', 'example');
  await client.delWhere('entries', 'race_id=eq.example');
  assert.equal(requests[0].headers.Authorization, 'Bearer first-session');
  assert.ok(requests.slice(1).every(request => request.headers.Authorization === 'Bearer refreshed-session'));
  token = null;
  await assert.rejects(client.post('entries', {}), /Signed out/);
  assert.equal(requests.length, 5);
  await scope.window.RaceNight.createClient('https://example.com', 'public-key').get('races');
  assert.equal(requests[5].headers.Authorization, 'Bearer public-key');
});

test('only explicitly idempotent creates retry a transient failure', async () => {
  const scope = { window: {}, URL, setTimeout: callback => callback() };
  let attempts = 0;
  scope.fetch = async () => {
    attempts++;
    return attempts % 2 ? { ok: false, status: 503, text: async () => 'Unavailable' } : { ok: true, json: async () => 'entry-id' };
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const client = scope.window.RaceNight.createClient('https://example.com', 'public-key');
  await assert.rejects(client.post('entries', {}), /Unavailable/);
  assert.equal(attempts, 1);
  attempts = 0;
  assert.equal(await client.post('rpc/submit_signup', { p_client_ref: 'stable-ref' }, { attempts: 4 }), 'entry-id');
  assert.equal(attempts, 2);
});

test('a cancelled board read aborts instead of retrying an expired snapshot', async () => {
  const scope = { window: {}, URL };
  let attempts = 0;
  scope.fetch = async (_url, { signal }) => {
    attempts++;
    signal.throwIfAborted();
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const client = scope.window.RaceNight.createClient('https://example.com', 'public-key');
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(client.get('live_state', '', { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(attempts, 1);
});

test('anonymous walk-up creation returns selected safe fields without requesting private columns', async () => {
  const scope = { window: {}, URL };
  scope.fetch = async (url, options) => {
    assert.equal(url, 'https://example.com/rest/v1/entries?select=id');
    assert.equal(options.headers.Authorization, 'Bearer public-key');
    assert.equal(options.headers.Prefer, 'return=representation');
    assert.deepEqual(JSON.parse(options.body), { name: 'Walk-up', client_ref: 'stable-ref' });
    return { ok: true, json: async () => [{ id: 'entry-id' }] };
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const client = scope.window.RaceNight.createClient('https://example.com', 'public-key');
  assert.deepEqual(await client.post('entries', { name: 'Walk-up', client_ref: 'stable-ref' }, { select: 'id' }), [{ id: 'entry-id' }]);
});
