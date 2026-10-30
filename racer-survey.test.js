const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function survey({ fail = false, storageBlocked = false } = {}) {
  const stored = new Map(), requests = [], receipts = new Map();
  const state = { fail, valid: true };
  let children;
  const root = {
    dataset: {},
    set innerHTML(value) { this.html = value; children = new Map(); },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, {
        disabled: false, hidden: false, listeners: {},
        addEventListener(event, handler) { this.listeners[event] = handler; },
        reportValidity: () => state.valid, focus() {}
      });
      return children.get(selector);
    }
  };
  const window = { localStorage: {
    getItem(key) { if (storageBlocked) throw new Error('Blocked'); return stored.get(key); },
    setItem(key, value) { if (storageBlocked) throw new Error('Blocked'); stored.set(key, value); }
  }, RaceNight: { escapeHtml: text => text } };
  runInNewContext(readFileSync(`${__dirname}/racer-survey.js`, 'utf8'), { window });
  const options = {
    newRef: () => '10000000-0000-4000-8000-000000000001',
    post: async (path, body) => {
      assert.equal(path, 'rpc/submit_racer_survey');
      requests.push(structuredClone(body));
      if (!receipts.has(body.p_request_id)) receipts.set(body.p_request_id, structuredClone(body.p_answers));
      if (state.fail) throw new Error('Reply lost after saving');
      return body.p_request_id;
    }
  };
  window.RacerSurvey.mount(root, options);
  return { root, stored, requests, receipts, state,
    submit: () => root.querySelector('form').listeners.submit({ preventDefault() {} }),
    pick: value => root.querySelector('form').listeners.change({ target: { value } }),
    back: () => root.querySelector('#survey-back').listeners.click(),
    remount: () => { root.dataset = {}; window.RacerSurvey.mount(root, options); }
  };
}

const choices = ['occasionally', 'people', 'prep_time', 'three_to_six', 'tires', 'less_prep'];

test('six-choice feedback supports going back and sends only anonymous answers', async () => {
  const app = survey();
  app.state.valid = false;
  await app.submit();
  assert.equal(app.requests.length, 0);
  app.state.valid = true;
  app.pick('regularly'); await app.submit();
  app.back(); app.pick(choices[0]); await app.submit();
  for (const choice of choices.slice(1)) { app.pick(choice); await app.submit(); }
  assert.equal(app.receipts.size, 1);
  assert.deepEqual(app.requests[0], {
    p_request_id: '10000000-0000-4000-8000-000000000001',
    p_answers: { attendance: 'occasionally', enjoyment: 'people', barrier: 'prep_time',
      prep_time: 'three_to_six', prep_task: 'tires', improvement: 'less_prep' }
  });
  assert.match(app.root.html, /Thanks for your feedback/);
  assert.deepEqual([...app.stored.values()], ['true'], 'no answers or request reference stored in browser');
  app.remount();
  assert.match(app.root.html, /Thanks for your feedback/);
  assert.equal(app.requests.length, 1);
});

test('an uncertain submission locks its answers and safe retries save just one response', async () => {
  const app = survey({ fail: true, storageBlocked: true });
  for (const choice of choices) { app.pick(choice); await app.submit(); }
  assert.equal(app.receipts.size, 1);
  assert.equal(app.root.querySelector('fieldset').disabled, true);
  assert.match(app.root.querySelector('#survey-feedback').textContent, /couldn’t confirm/);
  app.state.fail = false;
  await Promise.all([app.submit(), app.submit()]);
  assert.deepEqual(app.requests[1], app.requests[0]);
  assert.equal(app.requests.length, 2);
  assert.equal(app.receipts.size, 1);
  assert.match(app.root.html, /Thanks for your feedback/);
});

test('public admin summary shows all choices, correct percentages, empty results and refresh failure recovery', async () => {
  const elements = new Map(), requests = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '', setAttribute() {} });
    return elements.get(id);
  };
  const state = { fail: false, summary: { total_responses: 3, counts: [
    { question: 'attendance', answer: 'occasionally', response_count: 2 },
    { question: 'attendance', answer: 'never', response_count: 1 },
    { question: 'prep_task', answer: 'engine', response_count: 3 }
  ] } };
  const scope = {
    window: { addEventListener() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'public-key' }) } },
    document: { getElementById: id => id === 'offline-banner' ? null : el(id), querySelectorAll: () => [], addEventListener() {} },
    navigator: { onLine: true }, setTimeout, clearTimeout,
    fetch: async url => {
      requests.push(url);
      return { ok: !state.fail, status: state.fail ? 403 : 200,
        text: async () => 'Unavailable', json: async () => state.summary };
    }
  };
  for (const file of ['race-night-client.js', 'racer-survey.js']) runInNewContext(readFileSync(`${__dirname}/${file}`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/admin/index.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  const load = () => runInNewContext('loadSurveyResults()', scope);
  await load();
  assert.match(el('survey-results-root').innerHTML, /3 responses/);
  assert.match(el('survey-results-root').innerHTML, /A few race nights<\/span><span>2 · 66.7%/);
  assert.match(el('survey-results-root').innerHTML, /I haven’t raced here yet<\/span><span>1 · 33.3%/);
  assert.match(el('survey-results-root').innerHTML, /Engine maintenance<\/span><span>3 · 100%/);
  assert.equal((el('survey-results-root').innerHTML.match(/class="survey-answer"/g) || []).length, 34);
  state.summary = { total_responses: 0, counts: [] };
  await load();
  assert.match(el('survey-results-root').innerHTML, /No responses yet/);
  assert.doesNotMatch(el('survey-results-root').innerHTML, /NaN|Infinity|66.7/);
  state.fail = true;
  await load();
  assert.match(el('survey-results-root').innerHTML, /Couldn’t load survey results/);
  assert.doesNotMatch(el('survey-results-root').innerHTML, /0 responses/);
  assert.equal(el('survey-results-refresh').disabled, false);
  state.fail = false;
  state.summary = { total_responses: 1, counts: [] };
  await load();
  assert.match(el('survey-results-root').innerHTML, /1 response<\/h2>/);
  assert.ok(requests.every(url => url.endsWith('/rpc/racer_survey_summary?')));
});
