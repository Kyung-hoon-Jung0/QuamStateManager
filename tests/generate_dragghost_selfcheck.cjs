// Wiring-step drag ghost + monitor (r15 CG2, docs/70).
//
// The complaint: dragging a port in the step-5 Instrument Wiring diagram gave
// NO cursor-following affordance at all — onWireDragStart preventDefault()s
// (killing even the browser's native drag snapshot) and the only feedback was
// the target-ring recolor + the docked monitor bar. Users asked for the port
// icon to move with the mouse, and for the which-port panel to be bigger and
// clearer.
//
// The fix: a body-appended #gen-drag-ghost (position:fixed, pointer-events:
// none, zoom-corrected clientX/Y follow, validity-tinted) created on drag
// start, moved on every mousemove, removed on drop/cancel; the monitor grew
// (0.84em → 0.95em) and became position:sticky so it stays visible while the
// tall diagram scrolls.
//
// Run: node tests/generate_dragghost_selfcheck.cjs   (needs jsdom)
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
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');
const CSS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {},
    attach(el) { try { el.type = 'text'; } catch (e) {} },
    format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};   // stub: keeps our fake ports
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  // jsdom has no layout → no elementFromPoint; the drag handlers call it on
  // every move/drop. null = "no port under the cursor", which is exactly the
  // ghost-only path this harness exercises.
  if (!win.document.elementFromPoint) {
    win.document.elementFromPoint = function () { return null; };
  }
  new win.Function(GEN_JS).call(win);
  return win;
}

const win = makeWorld();
const doc = win.document;
const G = win.QuamGen;
const T = G._test;

ok(typeof T.attachWiringDrag === 'function', 'G0: _test.attachWiringDrag exported');

// CSS pins — ghost styling + the enlarged sticky monitor
const ghostRule = (CSS.match(/#gen-drag-ghost\s*\{[^}]*\}/) || [''])[0];
ok(/position:\s*fixed/.test(ghostRule), 'G1: ghost is position:fixed');
ok(/pointer-events:\s*none/.test(ghostRule), 'G1: ghost is pointer-events:none (never steals the drop)');
const monRule = (CSS.match(/\.gen-wiring-monitor\s*\{[^}]*\}/) || [''])[0];
ok(/position:\s*sticky/.test(monRule), 'G2: monitor is sticky (visible during long drags)');
ok(/font-size:\s*0\.9[5-9]em|font-size:\s*1em/.test(monRule),
  'G2: monitor text enlarged (was 0.84em ≈ 12.6px)');

// Plant a fake wiring diagram (renderInstrumentWiring is stubbed, so these
// nodes survive) and bind the drag handlers to it.
const host = doc.getElementById('gen-wiring-diagram');
ok(!!host, 'G3: #gen-wiring-diagram exists');
host.innerHTML =
  '<div class="iw-port" data-con="1" data-slot="2" data-port="3" data-io="output">' +
  '  <span class="iw-port-circle" data-element="qA1" data-role="xy"></span>' +
  '</div>';
T.attachWiringDrag();

function mouse(type, target, x, y) {
  const ev = new win.MouseEvent(type, { bubbles: true, cancelable: true,
                                        clientX: x, clientY: y });
  target.dispatchEvent(ev);
}

const circle = host.querySelector('.iw-port-circle');

// G4: mousedown → ghost appears on <body>, labelled element · role, at cursor+offset
mouse('mousedown', circle, 100, 120);
let ghost = doc.getElementById('gen-drag-ghost');
ok(!!ghost, 'G4: ghost created on drag start');
ok(ghost && ghost.parentNode === doc.body, 'G4: ghost is body-appended (escapes clipping ancestors)');
ok(ghost && /qA1/.test(ghost.textContent) && /xy/.test(ghost.textContent),
  'G4: ghost names the dragged element · role (got "' + (ghost && ghost.textContent) + '")');
ok(ghost && ghost.style.left === '114px' && ghost.style.top === '132px',
  'G4: ghost at cursor + offset (got ' + (ghost && ghost.style.left) + ',' + (ghost && ghost.style.top) + ')');

// G5: mousemove → the ghost follows the cursor
mouse('mousemove', doc, 200, 220);
ok(ghost.style.left === '214px' && ghost.style.top === '232px',
  'G5: ghost follows mousemove (got ' + ghost.style.left + ',' + ghost.style.top + ')');

// G6: html CSS zoom (quam_ui_scale) — coordinates divided back out
doc.documentElement.style.zoom = '1.25';
mouse('mousemove', doc, 200, 220);
ok(ghost.style.left === (214 / 1.25) + 'px',
  'G6: ghost zoom-corrected (got ' + ghost.style.left + ')');
doc.documentElement.style.zoom = '';

// G7: mouseup ends the drag and removes the ghost
mouse('mouseup', doc, 200, 220);
ok(!doc.getElementById('gen-drag-ghost'), 'G7: ghost removed on drop');

// G8: Escape cancels and removes the ghost too
mouse('mousedown', circle, 50, 60);
ok(!!doc.getElementById('gen-drag-ghost'), 'G8: ghost re-created');
doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
ok(!doc.getElementById('gen-drag-ghost'), 'G8: Escape removes the ghost');

// G9: the whole-feedline grip wears the "feedline" label
host.innerHTML =
  '<div class="iw-port" data-con="1" data-slot="2" data-port="1" data-io="output">' +
  '  <span class="iw-port-grip"></span>' +
  '</div>';
T.attachWiringDrag();
mouse('mousedown', host.querySelector('.iw-port-grip'), 10, 10);
ghost = doc.getElementById('gen-drag-ghost');
ok(ghost && /feedline/.test(ghost.textContent), 'G9: grip drag says "feedline"');
mouse('mouseup', doc, 10, 10);

/* ── docs/135: the PORT ITSELF rides with the cursor ────────────────────
 * The label box alone read as "some text is following me" rather than "I am
 * holding this port". The ghost now carries a CLONE of the port's own
 * circle + label out of the rack (cloned, never re-drawn — colour, radius,
 * truncation and font sizing live in app.js's _appendPortCircle).
 * jsdom has no layout, so the geometry the code reads is stubbed here; the
 * cases above, whose nodes have no getBBox at all, are simultaneously the
 * pin that a realm without it still gets the old dot instead of a throw. */
const SVG_NS = 'http://www.w3.org/2000/svg';

function plantRack(opts) {
  const cell = doc.createElementNS(SVG_NS, 'g');
  cell.setAttribute('class', 'iw-port');
  ['con', 'slot', 'port', 'io'].forEach((k, i) =>
    cell.setAttribute('data-' + k, ['1', '2', '3', 'output'][i]));
  const circles = [];
  (opts.elements || ['qA1']).forEach((name) => {
    const g = doc.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', 'iw-port-circle');
    g.setAttribute('data-element', name);
    g.setAttribute('data-role', 'xy');
    const c = doc.createElementNS(SVG_NS, 'circle');
    c.setAttribute('fill', '#e67e22');
    c.setAttribute('r', '21');
    g.appendChild(c);
    const t = doc.createElementNS(SVG_NS, 'text');
    t.textContent = name;
    g.appendChild(t);
    stubGeometry(g, opts.onScreen == null ? 42 : opts.onScreen);
    cell.appendChild(g);
    circles.push(g);
  });
  if (opts.grip) {
    const grip = doc.createElementNS(SVG_NS, 'rect');
    grip.setAttribute('class', 'iw-port-grip');
    stubGeometry(grip, 8);
    cell.appendChild(grip);
  }
  stubGeometry(cell, 90);
  host.innerHTML = '';
  const svg = doc.createElementNS(SVG_NS, 'svg');
  svg.appendChild(cell);
  host.appendChild(svg);
  T.attachWiringDrag();
  return { cell, circles, grip: cell.querySelector('.iw-port-grip') };
}

// bbox and client rect must DIFFER, or an assert on the rendered size cannot
// tell "sized from the on-screen rect" (what the code must do, and what the UI
// zoom bug was about) from "sized from the bbox". Non-square too, so an
// aspect error is visible instead of structurally invisible.
function stubGeometry(el, px, ratio) {
  const h = Math.round(px * (ratio == null ? 0.75 : ratio));
  el.getBBox = () => ({ x: 44, y: 59, width: 42, height: 42 });
  el.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0,
                                      right: px, bottom: h, width: px, height: h });
}

// G10 — the ghost carries a real clone of the port, not a coloured dot.
// 84x63 on screen against a 42x42 bbox: the two sizing sources disagree,
// which is the only way an assert can tell them apart.
let rack = plantRack({ onScreen: 84 });
mouse('mousedown', rack.circles[0], 100, 120);
ghost = doc.getElementById('gen-drag-ghost');
let glyph = ghost && ghost.querySelector('svg.gen-drag-ghost-glyph');
ok(!!glyph, 'G10: the ghost carries a glyph svg');
ok(glyph && glyph.querySelector('circle') &&
   glyph.querySelector('circle').getAttribute('fill') === '#e67e22',
  "G10: the port's own circle (and its colour) came along");
ok(glyph && glyph.querySelector('text') &&
   glyph.querySelector('text').textContent === 'qA1',
  'G10: the circle brought its label with it');
ok(glyph && glyph.getAttribute('viewBox') === '42 57 46 46',
  'G10: viewBox is the source bbox + stroke padding (got ' +
  (glyph && glyph.getAttribute('viewBox')) + ')');
// rect is 84x63 while the bbox is 42x42: sizing from the bbox would give
// 46x46, so these two asserts are what separate the required behaviour from
// the plausible-looking wrong one.
ok(glyph && glyph.getAttribute('width') === '88',
  'G10: WIDTH comes from the on-screen rect, not the bbox ' +
  '(got ' + (glyph && glyph.getAttribute('width')) + ', bbox path would be 46)');
ok(glyph && glyph.getAttribute('height') === '67',
  'G10: HEIGHT follows the rect too - the glyph keeps the port aspect ' +
  '(got ' + (glyph && glyph.getAttribute('height')) + ')');
ok(ghost && ghost.classList.contains('gen-drag-ghost-hasglyph'),
  'G10: the box marks itself as carrying a glyph (so CSS can get out of its way)');
// The CLONE carries its own <text>qA1</text> inside the ghost, so testing
// ghost.textContent proves nothing about the label span. Read the span.
const lblEl = ghost && ghost.querySelector('.gen-drag-ghost-label');
ok(lblEl && /qA1/.test(lblEl.textContent) && /xy/.test(lblEl.textContent),
  'G10: the text LABEL survives - a fit-scaled circle is too small to read ' +
  '(got "' + (lblEl && lblEl.textContent) + '")');
// G11 — the original dims, and comes back when the drag is cancelled.
ok(rack.circles[0].classList.contains('iw-port-lifted'),
  'G11: the lifted port dims (it is not in two places at once)');
ok(!rack.cell.classList.contains('iw-port-lifted'),
  'G11: dimming is the CIRCLE, not the whole cell (peers on a shared port stay lit)');
doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
ok(!doc.querySelector('.iw-port-lifted'),
  'G11: Escape un-dims it — a cancelled drag must not leave a faded port');

// G12 — a feedline grip carries the WHOLE cell, not the grey grip bar alone.
rack = plantRack({ elements: ['qA1', 'qA2', 'qA3'], grip: true });
mouse('mousedown', rack.grip, 30, 30);
ghost = doc.getElementById('gen-drag-ghost');
glyph = ghost && ghost.querySelector('svg.gen-drag-ghost-glyph');
ok(glyph && glyph.querySelectorAll('circle').length === 3,
  'G12: the grip carries every qubit on the feedline (got ' +
  (glyph ? glyph.querySelectorAll('circle').length : 0) + ')');
ok(rack.cell.classList.contains('iw-port-lifted'),
  'G12: and the whole cell is what dims');
mouse('mouseup', doc, 30, 30);
ok(!doc.querySelector('.iw-port-lifted'), 'G12: drop un-dims the cell');

// G13 — a drop-target ring is about the TARGET; it must not be carried.
rack = plantRack({});
rack.circles[0].classList.add('iw-port-bad');
mouse('mousedown', rack.circles[0], 10, 10);
glyph = doc.getElementById('gen-drag-ghost').querySelector('svg.gen-drag-ghost-glyph');
ok(glyph && !glyph.querySelector('.iw-port-bad'),
  'G13: a stale validity ring is stripped from the carried copy');
mouse('mouseup', doc, 10, 10);

// G14 — a speck is not an affordance: a tiny on-screen port is floored.
rack = plantRack({ onScreen: 12 });
mouse('mousedown', rack.circles[0], 10, 10);
glyph = doc.getElementById('gen-drag-ghost').querySelector('svg.gen-drag-ghost-glyph');
ok(glyph && Number(glyph.getAttribute('width')) >= 24,
  'G14: a fit-scaled sub-circle is floored to a visible size (got ' +
  (glyph && glyph.getAttribute('width')) + ')');
mouse('mouseup', doc, 10, 10);

/* ── docs/135 ⑤: the QDAC trigger lines reach the wizard's diagram ──────
 * The wizard's allocation→diagram regroup knew only analog in/out, so on a
 * QDAC chip every "qt" digital trigger line the build was about to create
 * was dropped from the picture — /instrument showed a DIG column, the
 * wizard showed none. It must also preserve SHARING: the real 20Q chip
 * drives one OPX digital output per QDAC ext trigger input, three qubits
 * to a port, and three qubits on one port is ONE cell with three circles. */
const B = T.buildInstrumentData;
ok(typeof B === 'function', 'G16: _test.buildInstrumentData exported');
G.state.spec.instruments = { controllers: [{ con: 1, fems: [
  { slot: 1, fem: 'mw' }, { slot: 4, fem: 'lf' }] }] };
const data = B({
  qA1: { rr: [{ con: 1, slot: 1, port: 1, io_type: 'output', instrument_id: 'mw-fem' }],
         qt: [{ con: 1, slot: 4, port: 1, io_type: 'digital', instrument_id: 'lf-fem' }] },
  qA9: { qt: [{ con: 1, slot: 4, port: 1, io_type: 'digital', instrument_id: 'lf-fem' }] },
  qA3: { qt: [{ con: 1, slot: 4, port: 2, io_type: 'digital', instrument_id: 'lf-fem' }] },
});
const fem4 = (((data.controllers || {})['1'] || {}).fems || {})['4'];
const fem1 = (((data.controllers || {})['1'] || {}).fems || {})['1'];
ok(!!fem4 && !!fem4.digital_ports, 'G16: the trigger FEM has a digital_ports bucket');
ok(fem4 && Object.keys(fem4.digital_ports || {}).length === 2,
  'G16: two SHARED ports, not one per qubit (got ' +
  (fem4 ? Object.keys(fem4.digital_ports || {}).length : 0) + ')');
ok(fem4 && (fem4.digital_ports['1'] || []).length === 2,
  'G16: both qubits on port 1 land in the SAME cell');
ok(fem4 && (fem4.digital_ports['1'] || []).every(a => a.role === 'digital'),
  'G16: a trigger wears the digital role (its own colour + the DIG column)');
ok(fem4 && !Object.keys(fem4.output_ports || {}).length,
  'G16: a digital line never leaks into the analog output column');
ok(fem1 && (fem1.output_ports['1'] || []).length === 1 &&
   fem1.output_ports['1'][0].role === 'rr',
  'G16: analog lines are untouched by the digital branch');
ok(fem1 && fem1.digital_ports && !Object.keys(fem1.digital_ports).length,
  'G16: a FEM with no triggers gets an EMPTY bucket, not a missing one');
// A step-3 FEM with nothing allocated to it must still be droppable AND
// carry the bucket, or renderInstrumentWiring reads undefined.
const bare = B({});
const bareFem = (((bare.controllers || {})['1'] || {}).fems || {})['4'];
ok(bareFem && bareFem.digital_ports && typeof bareFem.digital_ports === 'object',
  'G16: an empty step-3 FEM still has a digital_ports bucket');

// G17 — a trigger port is derived from spec.qdac, not spec.lines: showing it
// is right, offering to drag it is not.
host.innerHTML =
  '<div class="iw-port" data-con="1" data-slot="4" data-port="1" data-io="digital">' +
  '  <span class="iw-port-circle" data-element="qA1" data-role="digital"></span>' +
  '</div>' +
  '<div class="iw-port" data-con="1" data-slot="1" data-port="1" data-io="output">' +
  '  <span class="iw-port-circle" data-element="qA1" data-role="xy"></span>' +
  '</div>';
T.attachWiringDrag();
mouse('mousedown', host.querySelector('[data-io="digital"] .iw-port-circle'), 10, 10);
ok(!doc.getElementById('gen-drag-ghost'), 'G17: a digital trigger port is not draggable');
mouse('mouseup', doc, 10, 10);
mouse('mousedown', host.querySelector('[data-io="output"] .iw-port-circle'), 10, 10);
ok(!!doc.getElementById('gen-drag-ghost'),
  'G17: and the analog port beside it still is (the guard is not a blanket off-switch)');
mouse('mouseup', doc, 10, 10);

// G18 — the ghost lives in the ZOOMED coordinate space (G6 pins that for its
// position), so a size taken straight from getBoundingClientRect — which is
// already in screen px — gets scaled a second time and the carried port comes
// out uiZoom times too big at any quam_ui_scale other than 100%.
rack = plantRack({ onScreen: 84 });
doc.documentElement.style.zoom = '1.25';
mouse('mousedown', rack.circles[0], 10, 10);
glyph = doc.getElementById('gen-drag-ghost').querySelector('svg.gen-drag-ghost-glyph');
ok(glyph && glyph.getAttribute('width') === '71',
  'G18: the glyph divides the UI zoom back out (got ' +
  (glyph && glyph.getAttribute('width')) + ', un-divided would be 88)');
mouse('mouseup', doc, 10, 10);
doc.documentElement.style.zoom = '';

// G15 — CSS: the glyph must not be clipped by the svg root's default overflow.
const glyphRule = (CSS.match(/#gen-drag-ghost \.gen-drag-ghost-glyph\s*\{[^}]*\}/) || [''])[0];
ok(/overflow:\s*visible/.test(glyphRule),
  'G15: glyph svg is overflow:visible (the circle stroke sits outside the bbox)');
ok(/\.iw-port-lifted\s*\{[^}]*opacity/.test(CSS), 'G15: the lifted state has an opacity rule');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK generate_dragghost_selfcheck');
process.exit(0);
