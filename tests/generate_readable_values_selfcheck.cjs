'use strict';
const real = require('assert');
let count = 0;
const assert = Object.assign((...a) => { count++; return real(...a); },
  {equal: (...a) => { count++; return real.equal(...a); }, notEqual: (...a) => { count++; return real.notEqual(...a); }});
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const web = path.join(__dirname, '..', 'quam_state_manager/web');
const read = p => fs.readFileSync(path.join(web, p), 'utf8');
const dom = new JSDOM('<body></body>', {url: 'http://localhost/', runScripts: 'outside-only'});
const w = dom.window;
w.fetch = () => new Promise(() => {});
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) { return String(s || ''); }};
w.armPlainResize = () => {};
w.renderInstrumentWiring = () => {};
w.eval(read('static/generate.js').replace('  var STEP_COUNT = 8;',
  'window.paintEnvs = renderEnvList; var STEP_COUNT = 8;'));
w.document.body.innerHTML = read('templates/_generate.html');
const root = w.document.getElementById('generate-root');
const interpreter = 'C:\\ProgramData\\environments\\' + 'long_directory_'.repeat(8) + '/python.exe';
const raw = '<script>untrusted</script>';
w.QuamGen.state.env = interpreter;
w.QuamGen.state.spec.network.host = raw;
w.paintEnvs({envs: [{name: 'environment', python: interpreter}]});
w.QuamGen._test.enterReviewStep();
const kit = read('static/ui-kit.css');
// Keep the real kit declarations in order; vendor CSS has unsupported jsdom syntax.
w.document.head.innerHTML = '<style>' + kit + '</style>';
function width(n) {
  w.innerWidth = n;
  w.dispatchEvent(new w.Event('resize'));
}
function styles(node) { return w.getComputedStyle(node); }
for (const n of [400, 700, 701, 1440, 400]) {
  width(n);
  const narrow = n <= 700;
  assert.equal(root.classList.contains('ui-run-diff-compact'), narrow);
  const table = root.querySelector('.gen-review-table');
  const value = table.querySelector('td > span');
  const env = root.querySelector('.gen-env-path');
  if (process.argv[2] !== 'env') {
    assert.equal(value.textContent, interpreter);
    assert.equal(value.querySelectorAll('wbr').length > 0, narrow, 'review path joints');
    if (narrow) {
      assert.equal(styles(table).tableLayout, 'fixed', 'review stays inside pane');
      assert.equal(styles(table).width, '100%');
      assert.equal(styles(value).whiteSpace, 'normal');
      assert.equal(styles(value).overflowWrap, 'anywhere');
      assert.equal(styles(value).overflow, 'visible');
      for (const cell of table.querySelectorAll('td > span')) assert(cell.classList.contains('ui-dot-name'));
    } else assert.notEqual(styles(table).tableLayout, 'fixed');
    assert(!table.querySelector('script'));
    assert(table.textContent.includes(raw));
  }
  if (process.argv[2] !== 'review') {
    assert.equal(env.textContent, interpreter);
    assert.equal(env.querySelectorAll('wbr').length > 0, narrow, 'interpreter path joints');
    assert(env.classList.contains('ui-dot-name'), 'one name part for both values');
    if (narrow) {
      assert.equal(styles(env).whiteSpace, 'normal');
      assert.equal(styles(env).textOverflow, 'clip');
      assert.equal(styles(env).overflow, 'visible');
      assert.equal(styles(env).overflowWrap, 'anywhere');
    }
  }
}

// Joints: a path of only backslashes and a path of only slashes each get one joint per separator.
if (process.argv[2] !== 'review') {
  width(400);
  for (const p of ['C:\\envs\\deep\\tool\\python.exe', '/opt/envs/deep/tool/python.exe']) {
    const joints = p.split('').filter(c => c === '/' || c === '\\').length;
    w.paintEnvs({envs: [{name: 'environment', python: p}]});
    const e = root.querySelector('.gen-env-path');
    assert.equal(e.textContent, p);
    assert.equal(e.querySelectorAll('wbr').length, joints, 'one joint per separator in ' + p);
    e.querySelectorAll('wbr').forEach(j => {
      const before = j.previousSibling;
      assert(before && before.nodeType === 3 && /^[/\\]$/.test(before.nodeValue),
        'each joint sits right after its separator in ' + p);
    });
  }
}
// The wrap rule of the review header cell: read from the stylesheet by selector, then seen in effect.
if (process.argv[2] !== 'env') {
  const sheet = w.document.styleSheets[0];
  const rules = Array.from(sheet.cssRules).filter(r => r.selectorText &&
    r.selectorText.split(',').some(s => s.trim() === '.ui-generate.ui-run-diff-compact .gen-review-table th'));
  assert(rules.length > 0, 'review header wrap rule exists');
  assert(rules.some(r => r.style.getPropertyValue('white-space') === 'normal' &&
    r.style.getPropertyValue('overflow-wrap') === 'anywhere'), 'header cell wraps');
  width(400);
  const th = root.querySelector('.gen-review-table th');
  assert.equal(styles(th).whiteSpace, 'normal');
  assert.equal(styles(th).overflowWrap, 'anywhere');
}
// Mount: values present when the bundle mounts are fitted with no later event.
{
  const d2 = new JSDOM('<body></body>', {url: 'http://localhost/', runScripts: 'outside-only'});
  const w2 = d2.window;
  w2.fetch = () => new Promise(() => {});
  w2.NumberInput = w.NumberInput;
  w2.armPlainResize = () => {};
  w2.renderInstrumentWiring = () => {};
  w2.innerWidth = 400;
  w2.eval(read('static/generate.js'));
  w2.document.body.innerHTML = read('templates/_generate.html');
  w2.document.head.innerHTML = '<style>' + kit + '</style>';
  const r2 = w2.document.getElementById('generate-root');
  assert(!r2.classList.contains('ui-run-diff-compact'), 'not fitted before mount');
  w2.QuamGen.init();
  assert(r2.classList.contains('ui-run-diff-compact'), 'fitted at mount, no resize event');
  d2.window.close();
}
dom.window.close();
console.log('readable Generate values passed (' + count + ' assertions)');
