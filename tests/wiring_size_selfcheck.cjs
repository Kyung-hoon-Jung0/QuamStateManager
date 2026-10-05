/* jsdom selfcheck for the wiring size control (docs/290), running the REAL
 * shipped app.js renderer and the REAL generate.js wizard.
 *
 * The complaint: on the Instrument Wiring page the readout port labels (the
 * multiplexed feedline sub-circles, drawn at 7 px inside the rack) could not
 * be read, and the Generate Config wizard draws the same rack. The fix gives
 * a surface that opts in (renderInstrumentWiring(..., {sizeControl: name}))
 * a size bar above its rack: minus / slider / plus / percent / Fit / 1:1, the
 * Chip Status map's zoom control. The size is the rack's scale against its
 * natural drawing and is applied to the svg's width/height over its viewBox,
 * so labels are re-drawn bigger rather than stretched.
 *
 * Pins:
 *  S1. a mount WITHOUT the option is untouched: no size bar, the docs/135
 *      Fit/1:1 bar as before;
 *  S2. with the option and nothing recorded, the rack is sized EXACTLY as
 *      the plain mount (both the fit case and the below-the-floor 1:1 case),
 *      and the size bar replaces the Fit/1:1 bar;
 *  S3. the bar's parts and its readout of the current scale;
 *  S4. plus/minus walk the 0.25 grid from the CURRENT scale, magnify past
 *      1:1 on request, and clamp at both ends;
 *  S5. Fit and 1:1 are the docs/135 modes, and the active one is marked;
 *  S6. the slider resizes IN PLACE (the bar and slider nodes survive);
 *  S7. the choice persists across a re-render, per surface key;
 *  S8. surfaces are independent, and every rack of ONE surface follows;
 *  S9. a blocked localStorage never makes the buttons inert;
 *  S10. a corrupt or out-of-range record is no record;
 *  S11. with no per-surface record the shared Fit/1:1 choice still decides;
 *  S12. the note names the real situation (fitted / wider than the pane);
 *  S13. the control borrows the map control's button rule (Pico width trap);
 *  G1-G4. the wizard's step-5 rack and its step-6 copy both carry the bar
 *      under ONE key, follow one press, and keep the size across the
 *      re-render every wizard edit triggers.
 *
 * Run: node tests/wiring_size_selfcheck.cjs (driven by
 * tests/test_wiring_size.py).
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

const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const GEN_JS = fs.readFileSync(path.join(STATIC, 'generate.js'), 'utf8');
const CSS = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
const GEN_HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');

let fails = 0;
let checks = 0;
function ok(c, m) {
  checks++;
  if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); }
}

/* One jsdom window with the real app.js in it. Every global the code reads
 * bare is bridged onto the window (and the node global, for good measure). */
function makeWorld(bodyHtml) {
  const dom = new JSDOM('<!doctype html><html><body>' + bodyHtml + '</body></html>',
    { url: 'http://localhost/instrument', pretendToBeVisual: true, runScripts: 'outside-only' });
  const win = dom.window;
  global.window = win;
  global.document = win.document;
  global.CSS = win.CSS;
  global.CustomEvent = win.CustomEvent;
  global.Event = win.Event;
  global.KeyboardEvent = win.KeyboardEvent;
  try { global.navigator = win.navigator; } catch (e) { /* node's own */ }
  global.location = win.location;
  global.MutationObserver = win.MutationObserver;
  const noopObs = class { observe() {} disconnect() {} unobserve() {} };
  global.IntersectionObserver = win.IntersectionObserver = noopObs;
  // A real observer would fire on every style write; the code only needs it
  // to exist, and re-deciding is exercised by rendering/pressing again.
  global.ResizeObserver = win.ResizeObserver = noopObs;
  global.requestAnimationFrame = win.requestAnimationFrame = (f) => setTimeout(f, 0);
  const store = {};
  const ls = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  global.localStorage = ls;
  Object.defineProperty(win, 'localStorage', { value: ls, configurable: true });
  global.sessionStorage = ls;
  win.htmx = global.htmx = { ajax() {}, trigger() {}, process() {}, on() {} };
  win.fetch = global.fetch = () => new win.Promise(() => {});   // never settles
  win.eval(APP_JS);
  return { win, doc: win.document, store, ls };
}

/* The 8-FEM rack of tests/instrument_fit_selfcheck.cjs (3 MW + 5 LF, no
 * digital column -> 1356 px natural), with a multiplexed readout feedline
 * on FEM 1 port 1 -- the cell whose labels the complaint was about. */
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
  fems['1'].output_ports['2'] = ['q1', 'q2', 'q3', 'q4'].map((q) => (
    { role: 'rr', element: q, label: q + '.rr', port_type: 'mw-fem-output' }));
  return { controllers: { '1': { fems: fems, max_output_port: 8 } } };
}

/* ======================= World A: the renderer ========================= */
const A = makeWorld(
  '<div id="instrument-diagram"></div><div id="plain-diagram"></div>' +
  '<div id="gen-a"></div><div id="gen-b"></div>');
const { win, doc, store, ls } = A;

function host(id) { return doc.getElementById(id); }
function setW(id, px) {
  Object.defineProperty(host(id), 'clientWidth', { value: px, configurable: true });
}
function render(id, opts) { win.renderInstrumentWiring(id, rack(), {}, opts); }
function svg(id) { return host(id).querySelector('svg.instrument-svg'); }
function bar(id) { return host(id).querySelector('.iw-sizebar'); }
function btn(id, act) { return bar(id).querySelector('[data-iw-size="' + act + '"]'); }
function press(id, act) {
  btn(id, act).dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
}
function pct(id) { return bar(id).querySelector('.iw-size-pct').textContent; }
function sizing(id) {
  const s = svg(id).style;
  return s.width + '|' + s.height + '|' + s.maxWidth;
}
const OPT_I = { sizeControl: 'instrument' };
const KEY_I = 'quam_wiring_size_instrument';
const KEY_G = 'quam_wiring_size_generate';

setW('instrument-diagram', 1200);
render('instrument-diagram', OPT_I);
const nat = parseFloat(svg('instrument-diagram').dataset.natW);
const natH = parseFloat(svg('instrument-diagram').dataset.natH);
ok(nat === 1356, 'fixture rack is the 1356 px one (got ' + nat + ')');

/* -- S1: no option, no size bar, the old bar as before ----------------- */
setW('plain-diagram', Math.round(nat * 0.3));
render('plain-diagram');
ok(!host('plain-diagram').querySelector('.iw-sizebar'),
   'S1 a mount without sizeControl gets no size bar');
ok(!!host('plain-diagram').querySelector('.iw-fitbar .iw-fitbar-btn'),
   'S1 ...and keeps the docs/135 Fit/1:1 bar');

/* -- S2: default is today's picture exactly ---------------------------- */
[[0.7, 'fit'], [0.3, '1:1 below the floor'], [1.5, 'a rack narrower than its pane']]
  .forEach(([frac, label]) => {
    delete store[KEY_I];
    delete store.quam_instrument_fit;
    setW('instrument-diagram', Math.round(nat * frac));
    setW('plain-diagram', Math.round(nat * frac));
    render('instrument-diagram', OPT_I);
    render('plain-diagram');
    ok(sizing('instrument-diagram') === sizing('plain-diagram'),
       'S2 default sizing identical to the plain mount (' + label + '): ' +
       sizing('instrument-diagram') + ' vs ' + sizing('plain-diagram'));
    // Asked in EVERY case: the plain mount draws its Fit/1:1 bar exactly when
    // the rack overflows, so a check made only where it fits proves nothing.
    ok(!host('instrument-diagram').querySelector('.iw-fitbar'),
       'S2 the size bar replaces the Fit/1:1 bar, never two bars (' + label +
       '; the plain mount has ' + host('plain-diagram').querySelectorAll('.iw-fitbar').length + ')');
    ok(host('instrument-diagram').querySelectorAll('.iw-sizebar').length === 1,
       'S2 exactly one size bar (' + label + ')');
    ok(host('instrument-diagram').firstElementChild === bar('instrument-diagram'),
       'S2 the bar sits ABOVE the rack, first child of the host (' + label + ')');
  });
ok(host('plain-diagram').querySelectorAll('.iw-fitbar').length === 0,
   'S2 (control) the plain mount with a fitting rack has no bar either');

/* -- S3: the parts, and the readout of the current scale --------------- */
delete store[KEY_I];
setW('instrument-diagram', Math.round(nat * 0.7));   // fit at 70%
render('instrument-diagram', OPT_I);
const acts = Array.prototype.map.call(bar('instrument-diagram').querySelectorAll('[data-iw-size]'),
  (b) => b.getAttribute('data-iw-size'));
ok(JSON.stringify(acts) === JSON.stringify(['out', 'in', 'fit', '1']),
   'S3 buttons in order minus/plus/Fit/1:1 (got ' + JSON.stringify(acts) + ')');
const sl = bar('instrument-diagram').querySelector('input.iw-size-slider[type="range"]');
ok(sl && sl.min === '0.5' && sl.max === '3', 'S3 a 0.5..3 range slider');
ok(btn('instrument-diagram', 'out').textContent === '\u2212' &&
   btn('instrument-diagram', 'in').textContent === '+',
   'S3 minus/plus glyphs as on the map control');
ok(pct('instrument-diagram') === '70%', 'S3 the readout shows the fitted scale (got ' +
   pct('instrument-diagram') + ')');
ok(btn('instrument-diagram', 'fit').classList.contains('active'), 'S3 Fit is marked active');

/* -- S4: plus/minus on the 0.25 grid, magnify on request, clamp -------- */
press('instrument-diagram', 'in');
ok(store[KEY_I] === '0.75', 'S4 plus from a fitted 70% lands on 75% (got ' + store[KEY_I] + ')');
ok(svg('instrument-diagram').style.width === Math.round(nat * 0.75) + 'px' &&
   svg('instrument-diagram').style.height === Math.round(natH * 0.75) + 'px' &&
   svg('instrument-diagram').style.maxWidth === 'none',
   'S4 the rack is drawn at 75% of its natural size (' + sizing('instrument-diagram') + ')');
press('instrument-diagram', 'in');
press('instrument-diagram', 'in');
ok(store[KEY_I] === '1.25', 'S4 two more presses: 125% (got ' + store[KEY_I] + ')');
ok(svg('instrument-diagram').style.width === Math.round(nat * 1.25) + 'px',
   'S4 bigger than 1:1 when the viewer asks (width ' + svg('instrument-diagram').style.width + ')');
ok(pct('instrument-diagram') === '125%', 'S4 the readout follows (got ' + pct('instrument-diagram') + ')');
// Every press past an end must leave the end in place -- checked after each
// one, because an unclamped value is rejected on read and the walk restarts
// from the default, which a single check at the end can land on by parity.
const atTop = [];
for (let i = 0; i < 12; i++) { press('instrument-diagram', 'in'); if (i >= 7) atTop.push(store[KEY_I]); }
ok(atTop.every((v) => v === '3'), 'S4 plus clamps at 300% on every extra press (got ' + atTop + ')');
const atBottom = [];
for (let i = 0; i < 14; i++) { press('instrument-diagram', 'out'); if (i >= 9) atBottom.push(store[KEY_I]); }
ok(atBottom.every((v) => v === '0.5'), 'S4 minus clamps at 50% on every extra press (got ' + atBottom + ')');
ok(svg('instrument-diagram').style.width === Math.round(nat * 0.5) + 'px',
   'S4 ...and the rack follows down too');

/* -- S5: Fit and 1:1 ----------------------------------------------------- */
press('instrument-diagram', 'fit');
ok(store[KEY_I] === 'fit', 'S5 Fit is recorded');
ok(/^min\(100%, 1356px\)$/.test(svg('instrument-diagram').style.width) &&
   svg('instrument-diagram').style.maxWidth === nat + 'px',
   'S5 Fit is the docs/135 never-magnify fit (' + sizing('instrument-diagram') + ')');
ok(btn('instrument-diagram', 'fit').getAttribute('aria-pressed') === 'true' &&
   btn('instrument-diagram', '1').getAttribute('aria-pressed') === 'false',
   'S5 Fit pressed, 1:1 not');
press('instrument-diagram', '1');
ok(store[KEY_I] === '1' && svg('instrument-diagram').style.width === nat + 'px' &&
   svg('instrument-diagram').style.height === natH + 'px',
   'S5 1:1 is the natural size (' + sizing('instrument-diagram') + ')');
ok(btn('instrument-diagram', '1').classList.contains('active') &&
   !btn('instrument-diagram', 'fit').classList.contains('active'),
   'S5 1:1 marked active, Fit not');

/* -- S6: the slider resizes in place ----------------------------------- */
const barNode = bar('instrument-diagram');
const slNode = barNode.querySelector('.iw-size-slider');
slNode.value = '1.6';
slNode.dispatchEvent(new win.Event('input', { bubbles: true }));
ok(store[KEY_I] === '1.6', 'S6 the slider records its value (got ' + store[KEY_I] + ')');
ok(svg('instrument-diagram').style.width === Math.round(nat * 1.6) + 'px',
   'S6 ...and the rack follows it');
ok(bar('instrument-diagram') === barNode &&
   bar('instrument-diagram').querySelector('.iw-size-slider') === slNode,
   'S6 the bar and the slider under the pointer are the SAME nodes (no rebuild mid-drag)');

/* -- S7: persists across a re-render ----------------------------------- */
render('instrument-diagram', OPT_I);
ok(svg('instrument-diagram').style.width === Math.round(nat * 1.6) + 'px',
   'S7 a re-render keeps the chosen size');
ok(bar('instrument-diagram').querySelector('.iw-size-slider').value === '1.60' &&
   pct('instrument-diagram') === '160%',
   'S7 ...and the control shows it (slider ' +
   bar('instrument-diagram').querySelector('.iw-size-slider').value + ', ' + pct('instrument-diagram') + ')');

/* -- S8: per surface; every rack of one surface follows ----------------- */
delete store[KEY_G];
setW('gen-a', Math.round(nat * 0.7));
setW('gen-b', Math.round(nat * 0.7));
render('gen-a', { sizeControl: 'generate' });
render('gen-b', { sizeControl: 'generate' });
ok(/^min\(/.test(svg('gen-a').style.width),
   'S8 another surface is not moved by the first one\'s choice (' + sizing('gen-a') + ')');
press('gen-a', 'in');
ok(store[KEY_G] === '0.75' && store[KEY_I] === '1.6',
   'S8 a press records under its own surface only (' + store[KEY_G] + ', ' + store[KEY_I] + ')');
ok(svg('gen-b').style.width === Math.round(nat * 0.75) + 'px',
   'S8 the other rack of the SAME surface follows one press (' + sizing('gen-b') + ')');
ok(svg('instrument-diagram').style.width === Math.round(nat * 1.6) + 'px',
   'S8 ...and the other surface stays put');

/* -- S9: storage blocked ------------------------------------------------- */
const realGet = ls.getItem;
const realSet = ls.setItem;
ls.setItem = () => { throw new Error('site data blocked'); };
ls.getItem = () => { throw new Error('site data blocked'); };
press('instrument-diagram', 'in');
ok(svg('instrument-diagram').style.width === Math.round(nat * 1.75) + 'px',
   'S9 a blocked localStorage does not make plus inert (' + sizing('instrument-diagram') + ')');
press('instrument-diagram', 'fit');
ok(/^min\(/.test(svg('instrument-diagram').style.width) &&
   btn('instrument-diagram', 'fit').classList.contains('active'),
   'S9 ...nor Fit');
ls.getItem = realGet;
ls.setItem = realSet;
press('instrument-diagram', '1');     // storage works again: it is the record again
ok(store[KEY_I] === '1', 'S9 once storage works the press lands in it again');

/* -- S10: a corrupt or out-of-range record is no record ---------------- */
['9', '0.1', 'abc', ''].forEach((bad) => {
  store[KEY_I] = bad;
  delete store.quam_instrument_fit;
  setW('instrument-diagram', Math.round(nat * 0.7));
  setW('plain-diagram', Math.round(nat * 0.7));
  render('instrument-diagram', OPT_I);
  render('plain-diagram');
  ok(sizing('instrument-diagram') === sizing('plain-diagram'),
     'S10 record "' + bad + '" falls back to the default (' + sizing('instrument-diagram') + ')');
});

/* -- S11: the shared Fit/1:1 choice decides when the surface has none ---- */
delete store[KEY_I];
store.quam_instrument_fit = '0';
setW('instrument-diagram', Math.round(nat * 0.7));     // default here would be fit
render('instrument-diagram', OPT_I);
ok(svg('instrument-diagram').style.width === nat + 'px' &&
   btn('instrument-diagram', '1').classList.contains('active'),
   'S11 a recorded 1:1 still wins with no per-surface size (' + sizing('instrument-diagram') + ')');
delete store.quam_instrument_fit;

/* -- S12: the note says what is true ------------------------------------ */
function note(id) { return bar(id).querySelector('.iw-sizebar-note').textContent; }
delete store[KEY_I];
setW('instrument-diagram', Math.round(nat * 0.7));
render('instrument-diagram', OPT_I);
ok(/scaled to fit/.test(note('instrument-diagram')),
   'S12 fitted rack: "scaled to fit" (got "' + note('instrument-diagram') + '")');
press('instrument-diagram', 'in');
ok(/Wider than this pane/.test(note('instrument-diagram')),
   'S12 enlarged past the pane: "Wider than this pane" (got "' + note('instrument-diagram') + '")');
setW('instrument-diagram', nat * 2);
press('instrument-diagram', '1');
ok(note('instrument-diagram') === '',
   'S12 a rack that fits says nothing (got "' + note('instrument-diagram') + '")');

/* -- S13: the map control's button rule, Pico trap included -------------- */
const btnRule = (CSS.match(/(^|\n)[^\n{]*\.iw-size-btn[^{]*\{[^}]*\}/) || [''])[0];
ok(/\.topo-hero-zbtn/.test(btnRule) && /width:\s*auto/.test(btnRule) && /margin:\s*0/.test(btnRule),
   'S13 the size buttons share the map zoom buttons\' rule (width:auto, margin:0)');
const barRule = (CSS.match(/\.iw-sizebar\s*\{[^}]*\}/) || [''])[0];
ok(/position:\s*sticky/.test(barRule) && /left:\s*0/.test(barRule) &&
   /background:\s*var\(--pico-background-color\)/.test(barRule),
   'S13 the bar is sticky over a scrolling rack, on the theme background');

/* ======================= World B: the wizard ============================ */
const B = makeWorld('<div id="table-pane">' + GEN_HTML + '</div>');
const bw = B.win;
bw.NumberInput = {
  fit() {},
  attach(el) { try { el.type = 'text'; } catch (e) {} },
  format() {},
  strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); },
};
bw.armPlainResize = function () {};
bw.confirm = function () { return true; };
if (!bw.document.elementFromPoint) bw.document.elementFromPoint = function () { return null; };
new bw.Function(GEN_JS).call(bw);
const T = bw.QuamGen._test;
ok(typeof T.renderWiringDiagram === 'function' && typeof T.renderPopWiring === 'function',
   'G0 the wizard exposes both rack renders');

const ch = (port, io) => ({ con: 1, slot: 1, port: port, io_type: io, instrument_id: 'mw-fem' });
T.state.allocation = {
  q1: { rr: [ch(1, 'output'), ch(1, 'input')], xy: [ch(2, 'output')] },
  q2: { rr: [ch(1, 'output'), ch(1, 'input')], xy: [ch(3, 'output')] },
};
const step5 = bw.document.getElementById('gen-wiring-diagram');
const step6 = bw.document.getElementById('gen-pop-wiring-diagram');
T.renderWiringDiagram();
T.renderPopWiring();
const b5 = step5.querySelector('.iw-sizebar');
const b6 = step6.querySelector('.iw-sizebar');
ok(!!step5.querySelector('svg.instrument-svg') && !!b5 &&
   b5.getAttribute('data-iw-size-key') === 'generate',
   'G1 the step-5 rack carries the size bar under the "generate" key');
ok(!!b6 && b6.getAttribute('data-iw-size-key') === 'generate',
   'G2 the step-6 copy carries it too, same key');
const nat5 = parseFloat(step5.querySelector('svg.instrument-svg').dataset.natW);
b5.querySelector('[data-iw-size="1"]').dispatchEvent(new bw.MouseEvent('click', { bubbles: true }));
b5.querySelector('[data-iw-size="in"]').dispatchEvent(new bw.MouseEvent('click', { bubbles: true }));
ok(B.store[KEY_G] === '1.25' && !(KEY_I in B.store),
   'G3 a wizard press records under the wizard\'s key only (' + B.store[KEY_G] + ')');
ok(step6.querySelector('svg.instrument-svg').style.width === Math.round(nat5 * 1.25) + 'px',
   'G3 the step-6 copy follows the step-5 press (' +
   step6.querySelector('svg.instrument-svg').style.width + ')');
T.renderWiringDiagram();                    // what every wizard edit triggers
ok(step5.querySelector('svg.instrument-svg').style.width === Math.round(nat5 * 1.25) + 'px' &&
   step5.querySelector('.iw-size-pct').textContent === '125%',
   'G4 the size survives a wizard re-render');

/* -- G5: an editable feedline's grip never covers a circle -------------
 * The wizard's step-5 rack is editable: a port with 2+ qubits carries a drag
 * grip on its left. It was placed by the cell's nominal radius while 4+
 * circles spread wider, so it sat on the first circle and hid its label
 * ("qA1" read "A1", seen in real Chrome at every size). */
(function () {
  const gw = rack();
  const ports = gw.controllers['1'].fems['1'].output_ports;
  const feed = (n, tag) => Array.from({ length: n }, (_, i) => (
    { role: 'rr', element: tag + i, label: tag + i + '.rr', port_type: 'mw-fem-output' }));
  ports['3'] = feed(2, 'b'); ports['4'] = feed(3, 'c'); ports['5'] = feed(6, 'd');
  win.renderInstrumentWiring('gen-a', gw, {}, { editable: true });
  const cells = Array.from(host('gen-a').querySelectorAll('g.iw-port')).filter((c) => c.querySelector('.iw-port-grip'));
  ok(cells.length === 4, 'G5 every editable feedline (2, 3, 4 and 6 qubits) has a grip (' + cells.length + ')');
  let worst = Infinity;
  cells.forEach((c) => {
    const g = c.querySelector('.iw-port-grip');
    const gRight = +g.getAttribute('x') + +g.getAttribute('width');
    Array.from(c.querySelectorAll('circle')).forEach((ci) => {
      worst = Math.min(worst, (+ci.getAttribute('cx') - +ci.getAttribute('r')) - gRight);
    });
  });
  ok(worst >= 1, 'G5 no grip overlaps a circle (smallest gap ' + worst.toFixed(1) + ' px)');
  // ...and it stays inside its own 82 px output column (port 1 is a single circle at the centre)
  const colX = +host('gen-a').querySelector('g.iw-port[data-slot="1"][data-port="1"][data-io="output"] circle').getAttribute('cx');
  const lefts = cells.map((c) => +c.querySelector('.iw-port-grip').getAttribute('x'));
  ok(Math.min.apply(null, lefts) >= colX - 41, 'G5 every grip stays inside its column (leftmost ' +
     (Math.min.apply(null, lefts) - colX).toFixed(1) + ' px from the centre, edge at -41)');
  win.renderInstrumentWiring('gen-b', gw, {}, {});
  ok(!host('gen-b').querySelector('.iw-port-grip'), 'G5 a read-only rack carries no grip');
})();

// pretendToBeVisual keeps a rAF loop alive; exit from the write's callback
// so the summary line is never truncated on a Windows pipe.
win.close();
bw.close();
const summary = fails
  ? fails + ' check(s) failed\n'
  : 'wiring_size_selfcheck: all checks passed (' + checks + ' assertions)\n';
process.stdout.write(summary, function () { process.exit(fails ? 1 : 0); });
