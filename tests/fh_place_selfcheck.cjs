// v1.3.0 A4: the value-history panel opens BESIDE the pressed clock's column,
// so the other clocks of the inspector stay reachable (and a press on one
// retargets the open panel). Driven by tests/test_v130_fh_place.py.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}
const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/app.js'), 'utf8');
const start = app.indexOf('window.FieldHistory = (function () {');
const source = app.slice(start, app.indexOf('})();', start) + 5);
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
let checks = 0;
function check(value, message) { checks++; assert.ok(value, message); }

const ROWS = 8;
function world(vw, vh, colLeft) {
  const rows = [];
  for (let i = 0; i < ROWS; i++) {
    rows.push('<tr><td>k' + i + '</td><td><form class="inline-edit fh-host">'
      + '<input type="hidden" name="dot_path" value="qubits.q1.f' + i + '">'
      + '<input type="text" name="value" value="' + i + '">'
      + '<button type="button" class="field-hist-btn" id="c' + i + '">h</button></form></td></tr>');
  }
  const dom = new JSDOM('<!doctype html><body><table id="insp">' + rows.join('') + '</table></body>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'https://example.invalid/' });
  const w = dom.window;
  const geo = { vw, vh, colLeft };
  Object.defineProperty(w, 'innerWidth', { get: () => geo.vw, configurable: true });
  Object.defineProperty(w, 'innerHeight', { get: () => geo.vh, configurable: true });
  Object.defineProperty(w.HTMLElement.prototype, 'offsetHeight', { configurable: true,
    get() { return this.id === 'field-history-panel' ? 400 : 0; } });
  // the panel's rect is what its style says (jsdom has no layout)
  const rect0 = w.HTMLElement.prototype.getBoundingClientRect;
  w.HTMLElement.prototype.getBoundingClientRect = function () {
    if (this.id !== 'field-history-panel') return rect0.call(this);
    const l = parseFloat(this.style.left) || 0, t = parseFloat(this.style.top) || 0, wd = parseFloat(this.style.width) || 0;
    return { left: l, top: t, width: wd, height: 400, right: l + wd, bottom: t + 400, x: l, y: t };
  };
  for (let i = 0; i < ROWS; i++) {
    const b = w.document.getElementById('c' + i);
    b.getBoundingClientRect = () => ({ left: geo.colLeft, right: geo.colLeft + 16, width: 16, height: 18,
      top: 200 + 40 * i, bottom: 218 + 40 * i, x: geo.colLeft, y: 200 + 40 * i });
  }
  const requests = [];
  w.fetch = function (url) {
    requests.push(decodeURIComponent(String(url).split('path=')[1] || ''));
    return w.Promise.resolve({ text: () => w.Promise.resolve('<div class="fh-result">rows</div>') });
  };
  new w.Function(source).call(w);
  const panel = () => w.document.getElementById('field-history-panel');
  const box = () => {
    const p = panel();
    const left = parseFloat(p.style.left), width = parseFloat(p.style.width), top = parseFloat(p.style.top);
    return { left, right: left + width, width, top, bottom: top + 400, side: p.getAttribute('data-fh-side') };
  };
  // a clock is reachable when the panel does not cover its centre
  const covered = (i) => {
    const r = w.document.getElementById('c' + i).getBoundingClientRect(), b = box();
    const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    return panel().style.display !== 'none' && cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
  };
  const press = (i) => {
    const b = w.document.getElementById('c' + i);
    b.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true }));
    w.FieldHistory.openInspector(b);           // the button's onclick
  };
  return { w, geo, panel, box, covered, press, requests };
}

(async () => {
  // 1. The inspector on the right half (1600 px window, clocks at x=1400):
  //    the old placement slid the panel left over the clock column.
  let x = world(1600, 950, 1400);
  x.press(1);
  await settle();
  let b = x.box();
  check(x.panel().style.display === 'block', 'the panel opens');
  for (let i = 0; i < ROWS; i++) check(!x.covered(i), 'clock ' + i + ' stays reachable: ' + JSON.stringify(b));
  check(b.right <= 1400 - 8 + 0.5, 'its right edge stays clear of the clock column: ' + JSON.stringify(b));
  check(b.side === 'left', 'no room on the right: the panel goes to the LEFT of the clocks (' + b.side + ')');
  check(Math.abs(b.top - (240 - 6)) < 0.5, 'top-aligned with the pressed row: ' + b.top);
  check(b.width === 500, 'full width where it fits: ' + b.width);

  // 2. A press on another clock RETARGETS the open panel -- it neither closes
  //    nor needs a second press -- and the panel follows to the new row.
  x.press(5);
  await settle();
  b = x.box();
  check(x.panel().style.display === 'block', 'pressing another clock keeps the panel open');
  check(x.requests[x.requests.length - 1] === 'qubits.q1.f5', 'and loads THAT field: ' + x.requests.join(','));
  check(Math.abs(b.top - (200 + 40 * 5 - 6)) < 0.5, 'beside the new row: ' + b.top);
  for (let i = 0; i < ROWS; i++) check(!x.covered(i), 'after the retarget clock ' + i + ' is still reachable');

  // 3. Room on the right (a wide inspector, clocks at x=600): to the RIGHT.
  x = world(1600, 950, 600);
  x.press(0);
  await settle();
  b = x.box();
  check(b.side === 'right' && b.left >= 616 + 8 - 0.5 && b.width === 500,
        'room on the right: the panel opens to the right of the clocks ' + JSON.stringify(b));
  for (let i = 0; i < ROWS; i++) check(!x.covered(i), 'right side: clock ' + i + ' reachable');

  // 4. Near the bottom of the window the panel is lifted to fit, still beside.
  x = world(1600, 560, 1400);
  x.press(7);                                   // row top 480, window 560
  await settle();
  b = x.box();
  check(b.top === 560 - 400 - 8, 'clamped to the window by its own height: ' + b.top);
  for (let i = 0; i < ROWS; i++) check(!x.covered(i), 'lifted: clock ' + i + ' reachable');

  // 5. A window resize re-places it beside the (moved) column.
  x = world(1600, 950, 1400);
  x.press(2);
  await settle();
  x.geo.vw = 1200; x.geo.colLeft = 1000;
  x.w.dispatchEvent(new x.w.Event('resize'));
  b = x.box();
  check(b.right <= 1000 - 8 + 0.5 && b.side === 'left', 'after a resize it is beside the column again: ' + JSON.stringify(b));
  for (let i = 0; i < ROWS; i++) check(!x.covered(i), 'after resize: clock ' + i + ' reachable');

  // 6. No usable room on either side (a 500 px window): the old under/over
  //    placement stands rather than a sliver of a panel.
  x = world(500, 950, 240);
  x.press(0);
  await settle();
  b = x.box();
  check(b.side === 'below' && Math.abs(b.top - 224) < 0.5 && b.width === 484,
        'too narrow for a side panel: under the anchor, as before ' + JSON.stringify(b));

  console.log('ok (' + checks + ' assertions)');
  setTimeout(() => process.exit(0), 0);
})().catch(e => { console.error(e); process.exit(1); });
