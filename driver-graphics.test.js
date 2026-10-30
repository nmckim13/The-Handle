const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const context = vm.createContext({ window: {} });
vm.runInContext(readFileSync(join(__dirname, 'driver-graphics.js'), 'utf8'), context);
const graphics = context.window.DriverGraphics;

test('registered graphics and trimmed names retain their image', () => {
  assert.equal(graphics.hasGraphic(' Nolan McKim '), true);
  assert.match(graphics.numHtml('Nolan McKim', 7, 52), /src="\/nolan7.png"/);
  assert.match(graphics.numHtml('Nolan McKim', 7, 52), /height:52px/);
  assert.equal(graphics.hasGraphic(' Kelton '), true);
  assert.match(graphics.numHtml('Kelton', 73, 48), /src="\/kelton73.png"/);
  assert.ok(readFileSync(join(__dirname, 'kelton73.png')).length > 0);
  for (const [name, number, file] of [['Chase Bice', 18, 'chase18.png'], ['Daren Miller', 10, 'daren10.png']]) {
    assert.equal(graphics.hasGraphic(` ${name} `), true);
    const html = graphics.numHtml(name, number, 48);
    assert.ok(html.includes(`src="/${file}"`));
    assert.ok(html.includes(`alt="${number}"`));
    assert.deepEqual(readFileSync(join(__dirname, file)).subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  }
});

test('unknown and inherited property names render text, including zero and missing numbers', () => {
  for (const name of ['Unknown', 'constructor', '__proto__', 'toString', null]) {
    assert.equal(graphics.hasGraphic(name), false);
    assert.match(graphics.numHtml(name, 0), />0<\/span>$/);
  }
  for (const number of [null, undefined, '']) {
    assert.match(graphics.numHtml('Unknown', number), />\?<\/span>$/);
  }
});

test('number text and image attributes escape hostile markup', () => {
  const number = `"><img src=x onerror=alert('x')>&`;
  const text = graphics.numHtml('Unknown', number, 48);
  const image = graphics.numHtml('Nolan McKim', number, 48);
  assert.ok(text.includes('&quot;&gt;&lt;img'));
  assert.ok(image.includes('alt="&quot;&gt;&lt;img'));
  assert.ok(image.includes('data-num="&quot;&gt;&lt;img'));
  assert.ok(!text.includes('<img'));
  assert.equal((image.match(/<img/g) || []).length, 1);
  assert.ok(!image.includes("alert('x')"));
});

test('invalid sizes cannot escape attributes or styles', () => {
  for (const size of [`48" onload="alert(1)`, '48; color:red', -1, 0, NaN, Infinity]) {
    const html = graphics.numHtml('Nolan McKim', 7, size);
    assert.match(html, /data-size="48"/);
    assert.ok(!html.includes('alert'));
  }
  assert.match(graphics.numHtml('Nolan McKim', 7, 100000), /height:1000px/);
  assert.match(graphics.numHtml('Unknown', 7, '52'), /font-size:44px/);
});

test('broken image fallback re-escapes decoded data-num, never executes it as HTML', () => {
  const img = {
    getAttribute: key => ({ 'data-num': '<svg onload=alert(1)>', 'data-size': 'bad' })[key],
    outerHTML: ''
  };
  graphics.onImgError(img);
  assert.ok(img.outerHTML.includes('&lt;svg onload=alert(1)&gt;'));
  assert.ok(!img.outerHTML.includes('<svg'));
  assert.match(img.outerHTML, /font-size:36px/);
});
