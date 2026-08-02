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

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK generate_dragghost_selfcheck');
process.exit(0);
