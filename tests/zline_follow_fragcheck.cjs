/* w7 final-QA P2 -- an OPEN Z-line distortion page follows the working copy.
 *
 * The REAL zline.js + wc-moved.js under jsdom, fed the REAL server output: the
 * driver (tests/test_wc_moved_follow.py) renders /zline and /zline/data for a
 * chip BEFORE and AFTER a FIR edit and hands them over as a JSON file.
 *
 * Pins: after the tray's edit_seq moves (a Take live: _swapPendingTray fires
 * sm:tray-swapped; an Auto-Sync pull / foreign refresh: an htmx swap of the
 * tray), the page re-GETs /zline and swaps the table body (the FIR-sum cell
 * shows the new value), re-loads the SELECTED line with the SAME operation
 * and model, and the drawn output trace equals the served data. An event that
 * does not move the seq re-fetches nothing.
 * A *_fragcheck (not *_selfcheck): it needs the server-rendered fixture, so
 * npm run selfcheck does not run it; tests/test_wc_moved_follow.py does.
 * Run: node tests/zline_follow_fragcheck.cjs FIXTURE.json */
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const vm = require('vm');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else console.log('ok - ' + m); }

const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const LINE = FX.line, OP = FX.op;
let phase = 'before';
const dom = new JSDOM('<!doctype html><html><body>'
  + '<div id="pending-tray" data-edit-seq="seq-A"></div>'
  + '<main id="table-pane">' + FX.before.page + '</main></body></html>',
  { url: 'http://localhost/zline?line=' + encodeURIComponent(LINE), pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.CustomEvent = window.CustomEvent; global.Event = window.Event;
global.navigator = window.navigator; global.history = window.history;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.URL = window.URL;

const calls = [];
function serve(url) {
  const u = new window.URL(url, 'http://localhost');
  const P = FX[phase];
  if (u.pathname === '/zline') return { text: P.page };
  if (u.pathname === '/zline/data') {
    const key = u.searchParams.get('line') + '|' + (u.searchParams.get('model') || 'sum') + '|' + (u.searchParams.get('op') || '');
    const body = P.data[key];
    if (!body) throw new Error('fixture has no /zline/data for ' + key);
    return { json: body };
  }
  throw new Error('unexpected fetch ' + url);
}
global.fetch = window.fetch = function (url, opts) {
  calls.push({ url: String(url), headers: (opts && opts.headers) || {} });
  let r;
  try { r = serve(String(url)); } catch (e) { return Promise.reject(e); }
  return Promise.resolve({
    ok: true, status: 200,
    json: function () { return Promise.resolve(JSON.parse(JSON.stringify(r.json))); },
    text: function () { return Promise.resolve(r.text); },
  });
};
// Plotly is lazy-loaded in the app; here the render hook records what was drawn
window.requirePlotly = function () { return Promise.resolve(); };
window._plotlyRender = function (el, traces) { el.data = traces; return Promise.resolve(); };

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'wc-moved.js'), 'utf8'), { filename: 'wc-moved.js' });
vm.runInThisContext(fs.readFileSync(path.join(STATIC, 'zline.js'), 'utf8'), { filename: 'zline.js' });

const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
async function until(pred, ms) {
  const t = Date.now();
  while (Date.now() - t < (ms || 3000)) { if (pred()) return true; await tick(10); }
  return false;
}
const root = () => document.getElementById('zline-root');
const lastY = (id) => { const el = document.getElementById(id); const t = el && el.data; return t && t.length ? t[t.length - 1].y : null; };
const eq = (a, b) => !!a && !!b && a.length === b.length && a.every((v, i) => v === b[i]);
const firSum = () => {
  const tr = Array.prototype.find.call(document.querySelectorAll('.zline-row'), r => r.getAttribute('data-line') === LINE);
  return tr ? tr.children[4].textContent.trim() : null;
};
function moveTray(seq, how) {
  const old = document.getElementById('pending-tray');
  const t = document.createElement('div');
  t.id = 'pending-tray'; t.setAttribute('data-edit-seq', seq);
  old.parentNode.replaceChild(t, old);
  if (how === 'swap') document.dispatchEvent(new window.CustomEvent('sm:tray-swapped'));
  else if (how === 'htmx') t.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true }));
}
const pageGets = () => calls.filter(c => new window.URL(c.url, 'http://localhost').pathname === '/zline').length;

(async () => {
  const key = (ph) => LINE + '|sum|' + OP;
  ok(await until(() => (root().getAttribute('data-rendered') || '').indexOf(LINE + '|') === 0), 'the page draws the selected line');
  // the reader picks the second operation: the refresh must keep it
  const opSel = document.getElementById('zline-op');
  opSel.value = OP;
  opSel.dispatchEvent(new window.Event('change'));
  ok(await until(() => root().getAttribute('data-rendered') === LINE + '|' + OP + '|sum'), 'the chosen operation is drawn');
  const B = FX.before.data[key()], A = FX.after.data[key()];
  ok(eq(lastY('zline-step'), B.step.both) && eq(lastY('zline-pulse'), B.pulse.both), 'before: drawn == served');
  ok(firSum() === FX.before.fir_sum, 'before: the row shows FIR sum ' + FX.before.fir_sum + ' (got ' + firSum() + ')');
  ok(!eq(B.step.both, A.step.both) && FX.before.fir_sum !== FX.after.fir_sum, 'the fixture edit changes both the curve and the row');

  // an event that does NOT move the working copy fetches nothing
  const n0 = pageGets();
  document.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true }));
  document.body.dispatchEvent(new window.CustomEvent('liveDriftChanged', { bubbles: true }));
  await tick(200);
  ok(pageGets() === n0, 'no seq move: no re-GET of /zline');

  // 1) Take live: the server's working copy is now the AFTER chip; the sync
  //    swaps the tray through _swapPendingTray (sm:tray-swapped)
  phase = 'after';
  const r0 = root().getAttribute('data-refreshed');
  moveTray('seq-B', 'swap');
  ok(await until(() => root().getAttribute('data-refreshed') && root().getAttribute('data-refreshed') !== r0), 'Take live: the open page refreshes');
  const get = calls.filter(c => new window.URL(c.url, 'http://localhost').pathname === '/zline').pop();
  ok(get && get.headers['HX-Request'] === 'true', 'the table is re-read as the HX partial');
  ok(firSum() === FX.after.fir_sum, 'Take live: the row shows FIR sum ' + FX.after.fir_sum + ' (got ' + firSum() + ')');
  ok(eq(lastY('zline-step'), A.step.both), 'Take live: the drawn step == the served step');
  ok(eq(lastY('zline-pulse'), A.pulse.both), 'Take live: the drawn pulse == the served pulse');
  ok(root().getAttribute('data-selected') === LINE && document.getElementById('zline-op').value === OP
     && document.getElementById('zline-model').value === 'sum', 'selection, operation and model are kept');
  const sel = document.querySelector('.zline-row.zline-row-selected');
  ok(sel && sel.getAttribute('data-line') === LINE, 'the selected row is still marked');
  ok(root() && document.querySelectorAll('#zline-root').length === 1, 'the page is patched, not stacked');

  // 2) an undo arriving as an htmx swap of the tray (Auto-Sync pull, a
  //    foreign refresh): back to the BEFORE chip
  phase = 'before';
  const r1 = root().getAttribute('data-refreshed');
  moveTray('seq-C', 'htmx');
  ok(await until(() => root().getAttribute('data-refreshed') !== r1), 'an htmx tray swap refreshes too');
  ok(firSum() === FX.before.fir_sum && eq(lastY('zline-step'), B.step.both), 'undo: row and curve are back to the before values');

  console.log(fails ? fails + ' FAIL' : 'all ok');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
