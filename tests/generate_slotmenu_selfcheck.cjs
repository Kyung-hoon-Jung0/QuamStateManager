// Chassis FEM-chooser popup positioning (r15 CG3, docs/70).
//
// The bug: #gen-slot-menu was position:absolute inside #generate-root, whose
// containing block is #content-area (position:relative) — but openSlotMenu
// computed PAGE coordinates (window.scrollX + rect.left). The menu therefore
// landed offset by the sidebar width (user-resizable 160–640px!) + topbar +
// any banner, and the CSS zoom of quam_ui_scale multiplied the error. Users
// saw the MW-FEM/LF-FEM chooser "in a wrong place, far from the slot".
//
// The fix: .gen-slot-menu is position:fixed; openSlotMenu places it at the
// slot's viewport rect divided by the html CSS zoom (fixed elements inside a
// zoomed root get re-multiplied by the browser), and the menu closes when the
// #table-pane scroller scrolls (a fixed menu would otherwise stay pinned
// while the tiles move under it).
//
// Run: node tests/generate_slotmenu_selfcheck.cjs   (needs jsdom)
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
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  return win;
}

const win = makeWorld();
const G = win.QuamGen;
const T = G._test;

// S0: the internals under test are exported for this harness
ok(typeof T.openSlotMenu === 'function', 'S0: _test.openSlotMenu exported');
ok(typeof T.uiZoom === 'function', 'S0: _test.uiZoom exported');

// S1: CSS — the menu is FIXED-positioned (viewport coords), not absolute
const menuRule = (CSS.match(/\.gen-slot-menu\s*\{[^}]*\}/) || [''])[0];
ok(/position:\s*fixed/.test(menuRule),
  'S1: .gen-slot-menu must be position:fixed (was absolute → offset by #content-area)');

// S2: the source no longer mixes page-scroll offsets into the coordinates
const openBody = GEN_JS.slice(GEN_JS.indexOf('function openSlotMenu'),
                              GEN_JS.indexOf('function renderSlot'));
ok(!/window\.scroll[XY]/.test(openBody),
  'S2: openSlotMenu must not add window.scrollX/scrollY (fixed = viewport coords)');

// Boot a minimal chassis so the DOM has slot tiles.
G.hydrateFromSpec({
  network: { host: '1.2.3.4', cluster_name: 'C' },
  instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                 opx_plus: [], octaves: [] },
  qubits: ['qA1'], qubit_pairs: [], twpas: [],
  pair_gate: 'cz_tunable', lines: [],
  populate: { qubits: {}, pairs: {} }
}, { mode: 'regenerate' });

const doc = win.document;
const slot = doc.querySelector('#gen-chassis-list .gen-slot');
ok(!!slot, 'S3: chassis rendered a slot tile');
const menu = doc.getElementById('gen-slot-menu');
ok(!!menu, 'S3: #gen-slot-menu present in the template');

function stubRect(el, r) {
  el.getBoundingClientRect = function () {
    return Object.assign({ width: r.right - r.left, height: r.bottom - r.top,
                           x: r.left, y: r.top }, r);
  };
}

// S4: zoom 1 — menu lands AT the slot (left = rect.left, top = rect.bottom+4)
stubRect(slot, { left: 300, top: 200, right: 340, bottom: 240 });
T.openSlotMenu(slot, { con: 1 }, 1, 'mw');
ok(menu.hidden === false, 'S4: menu opened');
ok(menu.style.left === '300px', 'S4: left == rect.left at zoom 1 (got ' + menu.style.left + ')');
ok(menu.style.top === '244px', 'S4: top == rect.bottom+4 at zoom 1 (got ' + menu.style.top + ')');
T.hideSlotMenu();

// S5: html CSS zoom (quam_ui_scale) — fixed-element px are re-multiplied by
// the zoom, so the coords must be divided by it to land at the same spot.
doc.documentElement.style.zoom = '1.25';
ok(T.uiZoom() === 1.25, 'S5: uiZoom reads the html zoom');
T.openSlotMenu(slot, { con: 1 }, 1, 'mw');
ok(menu.style.left === '240px', 'S5: left == rect.left/zoom (got ' + menu.style.left + ')');
ok(menu.style.top === (244 / 1.25) + 'px', 'S5: top == (rect.bottom+4)/zoom (got ' + menu.style.top + ')');
doc.documentElement.style.zoom = '';
ok(T.uiZoom() === 1, 'S5: uiZoom falls back to 1 with no zoom set');
T.hideSlotMenu();

// S6: scrolling the #table-pane pane closes the menu (a fixed menu must not
// stay pinned while the chassis tiles scroll under it)
T.openSlotMenu(slot, { con: 1 }, 1, 'mw');
ok(menu.hidden === false, 'S6: menu open before scroll');
doc.getElementById('table-pane').dispatchEvent(new win.Event('scroll'));
ok(menu.hidden === true, 'S6: pane scroll closes the menu');

// S7: Escape still closes (keyboard path untouched)
T.openSlotMenu(slot, { con: 1 }, 1, 'mw');
menu.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
ok(menu.hidden === true, 'S7: Escape closes the menu');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK generate_slotmenu_selfcheck');
process.exit(0);
