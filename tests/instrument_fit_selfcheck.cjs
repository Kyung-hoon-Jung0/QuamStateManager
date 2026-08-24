/* jsdom selfcheck for the instrument-rack sizing fix (docs/135), running the
 * REAL app.js renderInstrumentWiring.
 *
 * The defect: the rack <svg> carried width="<natural>" + an inline
 * `max-width:100%` and NO viewBox. The element's BOX shrank to the host while
 * the drawing kept its own coordinates, so every FEM past the host width was
 * painted outside the visible box — and because the element itself fitted,
 * the host's `overflow-x:auto` never produced a scrollbar either. On the real
 * CQT 20Q chip (8 FEMs; 1884 px of rack with the DIG column, 1356 without)
 * both surfaces silently dropped the right-hand FEMs, and how many depended
 * on the pane: the user read "3 MW + 4 LF" off /instrument and "3 MW + 2 LF,
 * cut off" off the wizard, for one chip whose real inventory is 3 MW + 5 LF.
 * This fixture has no digital ports, so its rack measures 1356.
 *
 * Pins:
 *  A1. every FEM is in the DOM, and the svg has a viewBox and NO max-width
 *      crop (the regression itself);
 *  A2. narrow host, no stored preference -> FIT (whole rack scaled in);
 *  A3. very narrow host (below the legibility floor) -> 1:1, and the host is
 *      left able to scroll (max-width none, intrinsic px width);
 *  A4. the bar appears ONLY when the rack overflows, and its button flips the
 *      mode and persists it;
 *  A5. a stored preference beats the width-derived default, both ways.
 *
 * Run: node tests/instrument_fit_selfcheck.cjs (driven by
 * tests/test_instrument_fit.py).
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
let checks = 0;
// Speaks on success too: tests/run_selfchecks.cjs counts "ok - " lines, and a
// harness that only speaks on failure is reported as "(silent style)" —
// indistinguishable from one that asserted nothing.
function ok(c, m) {
  checks++;
  if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); }
}

const dom = new JSDOM(
  '<!doctype html><html><body><div id="instrument-diagram"></div></body></html>',
  { url: 'http://localhost/instrument', pretendToBeVisual: true });
const { window } = dom;

global.window = window;
global.document = window.document;
global.CSS = window.CSS;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
// node 21+ defines a read-only global navigator — bridge it only where it
// is still assignable, rather than crashing the harness on the getter.
try { global.navigator = window.navigator; } catch (e) { /* node's own */ }
global.location = window.location;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
// A REAL observer would fire on every style write we make below; the fit code
// only needs it to exist, and the re-decide path is exercised by calling
// render again.
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;

const store = {};
const ls = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
global.localStorage = ls;
Object.defineProperty(window, 'localStorage', { value: ls, configurable: true });
global.sessionStorage = ls;

window.htmx = { ajax() {}, trigger() {}, process() {}, on() {} };
global.htmx = window.htmx;
window.fetch = global.fetch = () => Promise.resolve(
  { status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') });

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

/* An 8-FEM OPX1000 rack, the shape query.py:get_instrument_wiring() emits:
 * 3 MW FEMs then 5 LF FEMs, exactly the CQT 20Q chip's inventory. */
function rack() {
  const fems = {};
  for (let slot = 1; slot <= 8; slot++) {
    fems[String(slot)] = {
      type: slot <= 3 ? 'mw-fem' : 'lf-fem',
      output_ports: { '1': [{ role: slot <= 3 ? 'xy' : 'z', element: 'q' + slot,
                              label: 'q' + slot, port_type: 'out' }] },
      input_ports: {},
    };
  }
  return { controllers: { '1': { fems: fems, max_output_port: 8 } } };
}

const host = window.document.getElementById('instrument-diagram');
function setHostWidth(px) {
  Object.defineProperty(host, 'clientWidth', { value: px, configurable: true });
}
function render() { window.renderInstrumentWiring('instrument-diagram', rack(), {}); }
function svg() { return host.querySelector('svg.instrument-svg'); }
function femLabels() {
  return Array.prototype.map.call(host.querySelectorAll('text'), (t) => t.textContent)
    .filter((t) => /\(..-fem\)/.test(t));
}
function bar() { return host.querySelector('.iw-fitbar'); }
// The fit branch caps at natural width, so it is no longer literally '100%'.
// max-width is the unambiguous discriminator: the cap in fit, 'none' at 1:1.
function isFit() { return svg().style.maxWidth !== 'none'; }
function btnText() { const b = bar() && bar().querySelector('.iw-fitbar-btn');
                     return b ? b.textContent : null; }

/* -- A1: nothing is dropped, and the crop is gone --------------------- */
setHostWidth(1200);
render();
const nat = parseFloat(svg().dataset.natW);
ok(femLabels().length === 8, 'A1 all 8 FEMs rendered, got ' + femLabels().length);
ok(femLabels().filter((l) => /lf-fem/.test(l)).length === 5,
   'A1 all 5 LF FEMs rendered');
ok(svg().getAttribute('viewBox') === '0 0 ' + nat + ' ' + svg().dataset.natH,
   'A1 svg carries a viewBox matching its natural size');
// The exact defect was a WIDTH LIMIT WITHOUT A VIEWBOX: that combination
// shrinks the box and crops the drawing. Limiting the width is fine once a
// viewBox makes the drawing scale with it.
ok(!(/max-width\s*:\s*100%/.test(svg().getAttribute('style') || '') &&
     !svg().getAttribute('viewBox')),
   'A1 no width limit is ever applied without a viewBox (the crop)');
ok(nat > 1200, 'A1 fixture rack is genuinely wider than a 1200px host (' + nat + ')');

/* -- A2: narrow host, no preference -> fit the WHOLE rack -------------- */
delete store.quam_instrument_fit;
setHostWidth(Math.round(nat * 0.7));          // 0.70 — above the 0.55 floor
render();
ok(isFit(), 'A2 fit mode scales the rack to the host');
ok(svg().style.height === 'auto', 'A2 fit mode keeps the aspect ratio');
ok(femLabels().length === 8, 'A2 all 8 FEMs still present in fit mode');

/* -- A3: below the legibility floor -> 1:1 and scrollable -------------- */
delete store.quam_instrument_fit;
setHostWidth(Math.round(nat * 0.3));          // 0.30 — below the floor
render();
ok(svg().style.width === nat + 'px', 'A3 below the floor the rack keeps its size');
ok(svg().style.maxWidth === 'none',
   'A3 no max-width, so the host can actually scroll (the whole point)');
ok(femLabels().length === 8, 'A3 all 8 FEMs still present at 1:1');

/* -- A4: the bar speaks only when it must, and it persists ------------- */
setHostWidth(nat + 200);                       // rack fits with room to spare
render();
ok(!bar(), 'A4 no fit bar when the rack fits on its own');
// NEVER magnify. `width:100%` on a viewBox'd svg scales UP as happily as
// down, so a rack narrower than its pane — every chip smaller than the 8-FEM
// one this was measured on — would be blown up past natural size in a picture
// whose whole job is to be a faithful rack. The earlier version of this very
// assertion pinned the magnified state as correct.
const fitW = svg().style.width;
// A bare "100%" is exactly the magnifying form — the ceiling has to be
// carried in the width itself (min(100%, Npx)) or as an absolute px, not
// inferred from a percentage that happens to parse below the natural width.
ok(/^min\(/.test(fitW) || (/px$/.test(fitW) && parseFloat(fitW) <= nat),
   'A4 a rack that already fits is never magnified (got width ' + fitW + ')');
ok(svg().style.maxWidth === nat + 'px',
   'A4 natural width is the ceiling (got max-width ' + svg().style.maxWidth + ')');

delete store.quam_instrument_fit;
setHostWidth(Math.round(nat * 0.3));           // 1:1 by default here
render();
ok(!!bar(), 'A4 the bar appears when the rack is wider than the pane');
const btn = bar().querySelector('.iw-fitbar-btn');
ok(btn && btn.textContent === 'Fit width', 'A4 1:1 offers "Fit width"');
btn.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(store.quam_instrument_fit === '1', 'A4 the press is remembered');
ok(isFit(), 'A4 the press takes effect immediately');
ok(bar().querySelector('.iw-fitbar-btn').textContent === '1:1',
   'A4 the bar now offers the way back');

/* -- A5: a recorded choice beats the width-derived default ------------- */
store.quam_instrument_fit = '0';
setHostWidth(Math.round(nat * 0.7));           // default here would be fit
render();
ok(svg().style.width === nat + 'px', 'A5 a stored 1:1 wins over the fit default');
store.quam_instrument_fit = '1';
setHostWidth(Math.round(nat * 0.3));           // default here would be 1:1
render();
ok(isFit(), 'A5 a stored fit wins over the 1:1 default');
ok(femLabels().length === 8, 'A5 no mode ever drops a FEM');

/* -- A6: the toggle works even when storage refuses the write ---------- */
setHostWidth(Math.round(nat * 0.3));
render();
if (isFit()) {                                  // land on 1:1 via the button
  bar().querySelector('.iw-fitbar-btn').dispatchEvent(
    new window.MouseEvent('click', { bubbles: true }));
}
ok(!isFit() && btnText() === 'Fit width', 'A6 starts at 1:1');
// Now refuse BOTH storage operations, as a browser with site data blocked does.
ls.setItem = () => { throw new Error('site data blocked'); };
ls.getItem = () => { throw new Error('site data blocked'); };
bar().querySelector('.iw-fitbar-btn').dispatchEvent(
  new window.MouseEvent('click', { bubbles: true }));
ok(isFit(), 'A6 a blocked localStorage does not make the button inert');
ok(btnText() === '1:1', 'A6 and the bar reflects the new mode');
ls.setItem = (k, v) => { store[k] = String(v); };
ls.getItem = (k) => (k in store ? store[k] : null);

// pretendToBeVisual keeps a rAF loop alive, so this harness has to say it is
// done explicitly — and process.exit() truncates a pending stdout write on a
// Windows pipe, which is exactly how a summary line goes missing and the
// runner reports "silent style". Exit from the write's own callback.
window.close();
const summary = fails
  ? fails + ' check(s) failed\n'
  : 'instrument_fit_selfcheck: all checks passed (' + checks + ' assertions)\n';
process.stdout.write(summary, function () { process.exit(fails ? 1 : 0); });
