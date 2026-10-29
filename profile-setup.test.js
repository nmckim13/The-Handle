const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

function editor() {
  const elements = new Map(), writes = [], uploads = [], files = new Map(), receipts = new Map();
  const drivers = [
    { id: 'alex', name: 'Alex', number: '07', photo_url: 'https://photos.test/original.png', bio: '  Original story  ', birthday: '1990-02-03', starts: 12, wins: 3, fav_condition: 'Tacky' },
    { id: 'morgan', name: 'Morgan', number: '19X', photo_url: null, fav_condition: null }
  ];
  const state = { consent: true, uploadLost: false, verifyFail: false, replyLost: false, readFail: false, corrupt: false };
  function el(id) {
    if (!elements.has(id)) {
      let value = '';
      const attrs = {};
      elements.set(id, { get value() { return value; }, set value(v) { value = String(v); }, style: {}, innerHTML: '', disabled: false, files: [], classList: { add() {}, remove() {} }, addEventListener() {}, appendChild() {}, setAttribute(k,v) { attrs[k] = v; }, getAttribute(k) { return k === 'src' ? this.src : attrs[k]; } });
    }
    return elements.get(id);
  }
  const radios = [{ value: 'Tacky', checked: false }, { value: 'Wet', checked: false }];
  class PhotoURL extends URL { static createObjectURL() { return 'blob:preview-' + Math.random(); } static revokeObjectURL() {} }
  const scope = {
    window: { addEventListener() {}, location: { origin: 'https://site.test' }, scrollTo() {}, AdminAuth: { create: () => ({ getAccessToken: async () => 'approved-staff' }) } },
    document: { getElementById: el, querySelectorAll: q => q.includes('fav_condition') ? radios : [], querySelector: () => null, createElement: tag => tag === 'canvas' ? { width: 0, height: 0, getContext: () => ({ drawImage() {} }), toBlob(callback) { callback(new Blob(['optimized'], { type: 'image/webp' })); } } : { value: '' } },
    URL: PhotoURL, Blob, Uint8Array, AbortController, crypto: require('node:crypto'), setTimeout, clearTimeout,
    createImageBitmap: async file => ({ width: file.width || 3200, height: file.height || 1200, close() {} }),
    confirm: () => state.consent, alert: message => { throw new Error('Unexpected alert: ' + message); },
    fetch: async (url, options = {}) => {
      const u = new URL(url), path = u.pathname;
      const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(body)), text: async () => JSON.stringify(body), arrayBuffer: async () => body instanceof Blob ? body.arrayBuffer() : new Blob([JSON.stringify(body)]).arrayBuffer() });
      if (path.includes('/storage/')) {
        const name = path.split('driver-photos/')[1];
        if (options.method === 'POST') {
          uploads.push({ name, options }); files.set(name, state.corrupt ? new Blob(['wrong']) : options.body);
          if (state.uploadLost) throw new Error('Upload response lost');
          return reply({ Key: name });
        }
        if (state.verifyFail) return reply({}, 403);
        return files.has(name) ? reply(files.get(name)) : reply({}, 404);
      }
      if (options.method === 'PATCH') {
        const body = JSON.parse(options.body); writes.push(body);
        Object.assign(drivers.find(d => u.searchParams.get('id') === 'eq.' + d.id), body);
        return reply([]);
      }
      if (options.method === 'POST') {
        assert.ok(path.endsWith('/rpc/set_driver_photo'));
        const body = JSON.parse(options.body); writes.push(body);
        if (!receipts.has(body.p_client_ref)) {
          const d = drivers.find(d => d.id === body.p_driver_id);
          if ((d.photo_url ?? null) !== body.p_expected) return reply({ message: 'Stale photo; refresh saved photo' }, 409);
          d.photo_url = body.p_photo_url;
          receipts.set(body.p_client_ref, { driver_id: d.id, client_ref: body.p_client_ref, photo_url: d.photo_url });
        }
        return state.replyLost ? reply({ message: 'Reply lost after commit' }, 403) : reply(receipts.get(body.p_client_ref));
      }
      if (state.readFail) return reply({ message: 'Read unavailable' }, 403);
      if (path.endsWith('/drivers')) return reply(drivers.filter(d => !u.searchParams.get('id') || u.searchParams.get('id') === 'eq.' + d.id));
      return reply([]);
    }
  };
  runInNewContext(readFileSync(`${__dirname}/race-night-client.js`, 'utf8'), scope);
  const html = readFileSync(`${__dirname}/profile-setup.html`, 'utf8');
  runInNewContext([...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].find(m => m[1].trim())[1], scope);
  const run = code => runInNewContext(code, scope);
  async function choose(id) { el('driver-select').value = id; await run('onDriverSelect()'); }
  async function select(file) { el('f-photo').files = [file]; await run('onPhotoSelected(document.getElementById("f-photo"))'); }
  return { el, state, drivers, writes, uploads, files, radios, run, choose, select };
}

function png(name = 'kart.png') { return Object.assign(new Blob([Uint8Array.from([137,80,78,71,13,10,26,10,0,0,0,0])], { type: 'image/png' }), { name }); }

test('photo drafts validate images, preserve the prior selection on rejection, and never follow another driver', async () => {
  const app = editor(); await app.choose('alex');
  await app.select(png()); assert.match(app.el('photo-meta').textContent, /1600 × 600 px/);
  const before = app.el('preview-img').src;
  await app.select(Object.assign(new Blob(['svg'], { type: 'image/svg+xml' }), { name: 'unsafe.svg' }));
  assert.equal(app.el('preview-img').src, before); assert.match(app.el('photo-message').textContent, /Previous photo\/selection kept/);
  await app.select(Object.assign(new Blob(['not a png'], { type: 'image/png' }), { name: 'fake.png' }));
  assert.equal(app.el('preview-img').src, before);
  await app.select(Object.assign(new Blob([new Uint8Array(5 * 1024 * 1024 + 1)], { type: 'image/png' }), { name: 'large.png' }));
  assert.equal(app.el('preview-img').src, before);
  app.state.consent = false; await app.choose('morgan'); assert.equal(app.el('driver-select').value, 'alex');
  app.state.consent = true; await app.choose('morgan');
  assert.equal(app.el('photo-save').disabled, true); assert.equal(app.el('photo-preview').hidden, true);
  assert.ok(app.radios.every(r => !r.checked));
  await app.run('savePhoto()'); assert.equal(app.uploads.length, 0); assert.equal(app.writes.length, 0);
});

test('uncertain storage and profile replies reuse one file and exact publication intent without touching profile details', async () => {
  const app = editor(); await app.choose('alex'); await app.select(png());
  app.el('f-bio').value = 'Unsaved text draft';
  app.state.uploadLost = app.state.verifyFail = true;
  await app.run('savePhoto()');
  assert.equal(app.uploads.length, 1); assert.equal(app.writes.length, 0);
  assert.equal(app.drivers[0].photo_url, 'https://photos.test/original.png');
  app.state.uploadLost = app.state.verifyFail = false; app.state.replyLost = true;
  await Promise.all([app.run('savePhoto()'), app.run('savePhoto()')]);
  assert.equal(app.uploads.length, 1); assert.equal(app.writes.length, 1);
  assert.equal(app.el('f-photo').disabled, true); assert.equal(app.el('photo-discard').disabled, true);
  app.state.replyLost = false; await app.run('savePhoto()');
  assert.deepEqual(app.writes[1], app.writes[0]); assert.equal(app.uploads.length, 1);
  assert.equal(app.uploads[0].options.headers['x-upsert'], 'false');
  assert.equal(app.uploads[0].options.headers.Authorization, 'Bearer approved-staff');
  assert.deepEqual(Object.keys(app.writes[0]).sort(), ['p_client_ref','p_driver_id','p_expected','p_photo_url']);
  assert.equal(app.el('f-bio').value, 'Unsaved text draft'); assert.equal(app.drivers[0].bio, '  Original story  ');
  assert.equal(app.drivers[0].birthday, '1990-02-03'); assert.equal(app.drivers[0].starts, 12);
  assert.match(app.el('photo-message').textContent, /saved — confirmed/);
});

test('photo conflicts require saved-photo review, removal keeps files, and mismatched upload bytes never publish', async () => {
  const app = editor(); await app.choose('alex'); await app.select(png());
  app.drivers[0].photo_url = 'https://photos.test/another.png';
  await app.run('savePhoto()'); assert.match(app.el('photo-message').textContent, /not confirmed/);
  app.state.readFail = true; await app.run('refreshPhoto()'); assert.equal(app.el('f-photo').disabled, true);
  app.state.readFail = false; await app.run('refreshPhoto()');
  assert.equal(app.el('photo-saved-review').hidden, false); assert.equal(app.el('saved-photo-img').src, 'https://photos.test/another.png');
  await app.run('savePhoto()'); assert.equal(app.writes.at(-1).p_expected, 'https://photos.test/another.png');
  assert.equal(app.uploads.length, 1);
  app.run('removePhoto()'); await app.run('savePhoto()');
  assert.equal(app.drivers[0].photo_url, null); assert.equal(app.files.size, 1);
  await app.select(png()); app.state.uploadLost = app.state.corrupt = true;
  const writes = app.writes.length; await app.run('savePhoto()');
  assert.equal(app.writes.length, writes); assert.equal(app.drivers[0].photo_url, null);
  assert.match(app.el('photo-message').textContent, /Stored file differs/);
});

test('public profile saves age without reading or rewriting private birthdays', async () => {
  const app = editor(); await app.choose('alex');
  app.el('f-age').value = '121';
  await app.run('submitProfile()');
  assert.equal(app.writes.length, 0);
  assert.match(app.el('driver-load-message').textContent, /0 to 120/);
  app.el('f-age').value = '0';
  await app.run('submitProfile()');
  assert.equal(app.writes.length, 1);
  assert.equal(app.writes[0].age, 0);
  assert.equal(Object.hasOwn(app.writes[0], 'birthday'), false);
  assert.equal(app.drivers[0].birthday, '1990-02-03');
});
