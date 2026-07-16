// Behavioral check for the Step-4 "Qubits" page readability redesign
// (flow bands; customer feedback: the page was hard to read and hid its two
// most important tools behind collapsed <details>).
//
// Pins: the Chip board + Qubit naming are ALWAYS visible (plain divs, board
// renders on step entry with zero toggling); the board empty-state at count
// 0; the partial-placement warning tint; Grid cols×rows sync from the zone;
// gate-aware pair headers (CZ neutral "Qubit ↔ Qubit" — roles are
// frequency-assigned at Populate; CR directional "Control → Target"); the
// manual-orientation chip; the read-only control-line confirmation block;
// the feedline-grouping summary; and the step-6 reference mirrors (LO map /
// topology / wiring) defaulting OPEN with the user's explicit collapse
// remembered.
//
// Run: node tests/generate_step4_layout_selfcheck.cjs   (needs jsdom)
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
const GRID_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'wiring-grid.js'), 'utf8');

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
  new win.Function(GRID_JS).call(win);
  new win.Function(GEN_JS).call(win);
  return win;
}

function setInput(win, idOrEl, value) {
  const el = typeof idOrEl === 'string' ? win.document.getElementById(idOrEl) : idOrEl;
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

function buildTo4(win, opts) {
  opts = opts || {};
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems =
    opts.mwOnly ? [{ slot: 1, fem: 'mw' }] : [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  if (opts.arch) {
    const arch = win.document.getElementById('gen-chip-arch');
    arch.value = opts.arch;
    arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  }
  return G;
}

// L1: no collapsibles on step 4 — board + naming are plain divs, the board
// renders on count entry alone, and the empty state shows at count 0.
(function alwaysVisible() {
  const win = makeWorld();
  const G = buildTo4(win);
  ok(win.document.getElementById('gen-topo').tagName === 'DIV',
    'L1: #gen-topo is a plain div');
  ok(win.document.getElementById('gen-naming').tagName === 'DIV',
    'L1: #gen-naming is a plain div');
  ok(win.document.querySelectorAll('.gen-panel[data-step="4"] details').length === 0,
    'L1: step 4 carries zero <details> collapsibles');
  // Count 0 → inert board says so instead of a dead grid.
  const board = win.document.getElementById('gen-topo-board');
  ok(board.textContent.indexOf('Set the qubit count') >= 0,
    'L1: empty state at count 0 (got: ' + board.textContent.slice(0, 40) + ')');
  // Setting the count renders the grid with no toggling anywhere.
  setInput(win, 'gen-qubit-count', '4');
  ok(!!win.document.querySelector('.gen-topo-grid'),
    'L1: grid rendered by the count change alone');
  // Naming controls visible + name chips present.
  ok(win.document.querySelectorAll('#gen-qubit-name-list input').length === 4,
    'L1: rename chips rendered without opening anything');
  // Grid dimension inputs mirror the (count-derived) zone.
  const z = win.WiringGrid.zone();
  ok(win.document.getElementById('gen-topo-cols').value == z.cols &&
     win.document.getElementById('gen-topo-rows').value == z.rows,
    'L1: Grid cols×rows inputs sync from the zone');
})();

// L2: partial placement tints the progress caption (pre-announces the gate).
(function partialPlacementWarn() {
  const win = makeWorld();
  const G = buildTo4(win);
  setInput(win, 'gen-qubit-count', '3');
  const cap = win.document.getElementById('gen-topo-caption');
  ok(!cap.classList.contains('gen-topo-caption-warn'),
    'L2: no warning tint with nothing placed');
  // Place ONE qubit (partial) — the board listens on mousedown/mouseup.
  const c0 = win.document.querySelector('.gen-topo-cell');
  c0.dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, clientX: 0, clientY: 0 }));
  c0.dispatchEvent(new win.MouseEvent('mouseup', { bubbles: true, clientX: 0, clientY: 0 }));
  ok(cap.textContent.indexOf('1/3') >= 0, 'L2: caption counts 1/3 placed');
  ok(cap.classList.contains('gen-topo-caption-warn'),
    'L2: partial placement tints the caption');
})();

// L3: gate-aware pair headers — CZ neutral (roles come from frequencies at
// Populate), CR directional; the manual pin chip surfaces on CZ rows.
(function gateAwareHeaders() {
  const win = makeWorld();
  const G = buildTo4(win);                       // default arch = CZ tunable
  setInput(win, 'gen-qubit-count', '3');
  const headText = function () {
    return win.document.querySelector('#gen-pair-list .gen-pair-head').textContent;
  };
  ok(/Qubit.*Qubit/.test(headText()) && headText().indexOf('Control') < 0,
    'L3: CZ header is neutral Qubit ↔ Qubit (got: ' + headText() + ')');
  ok(win.document.querySelector('#gen-pair-list .gen-pair-cz-hint')
       .textContent.indexOf('Populate') >= 0,
    'L3: one-line CZ caption defers roles to Populate');
  ok(!win.document.querySelector('.gen-pair-manual-chip'),
    'L3: no manual chip before a hand edit');
  // Hand-editing a dropdown pins the pair manual → chip appears.
  const tSel = win.document.querySelector('#gen-pair-list .gen-pair-t');
  tSel.value = 'q3';
  tSel.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(!!win.document.querySelector('.gen-pair-manual-chip'),
    'L3: manual chip appears after a hand-picked order');

  // CR chip: directional header, no CZ caption, no chips.
  const win2 = makeWorld();
  buildTo4(win2, { mwOnly: true, arch: 'fixed_frequency' });
  setInput(win2, 'gen-qubit-count', '3');
  const h2 = win2.document.querySelector('#gen-pair-list .gen-pair-head').textContent;
  ok(h2.indexOf('Control') >= 0 && h2.indexOf('Target') >= 0,
    'L3: CR header keeps Control → Target (got: ' + h2 + ')');
  ok(win2.document.querySelector('#gen-pair-list .gen-pair-link').textContent === '→',
    'L3: CR link glyph is directional');
  ok(!win2.document.querySelector('#gen-pair-list .gen-pair-cz-hint'),
    'L3: no CZ caption on a CR chip');
})();

// L4: the control-line confirmation block — explicitly labeled, read-only,
// derived from the architecture; the gate note echoes the live pair count.
(function confirmationBlock() {
  const win = makeWorld();
  const G = buildTo4(win);
  setInput(win, 'gen-qubit-count', '3');
  const why = win.document.querySelector('.gen-line-confirm-why');
  ok(!!why && /for confirmation/i.test(why.textContent),
    'L4: the block says it exists for confirmation');
  ok(win.document.getElementById('gen-chk-qubit-flux').disabled &&
     win.document.getElementById('gen-pair-gate').disabled,
    'L4: derived controls are read-only');
  ok(/applies to 2 pairs/.test(
       win.document.getElementById('gen-pair-gate-note').textContent),
    'L4: gate note echoes the live pair count (got: ' +
    win.document.getElementById('gen-pair-gate-note').textContent + ')');
  // Feedline-grouping summary confirms the mux choice.
  setInput(win, 'gen-mux-size', '2');
  const sum = win.document.getElementById('gen-qubit-summary').textContent;
  ok(/3 qubits · 2 feedlines/.test(sum),
    'L4: summary shows count + feedline grouping (got: ' + sum + ')');
})();

// L5: step-6 reference mirrors default OPEN; an explicit collapse ("0") is
// remembered per user.
(function mirrorsDefaultOpen() {
  const win = makeWorld();
  const G = buildTo4(win);
  setInput(win, 'gen-qubit-count', '2');
  // The topology mirror hides entirely while nothing is placed — give the
  // qubits board positions so all three reference panels are relevant.
  G.state.spec.populate.qubit = {
    q1: { grid_location: '0,0' }, q2: { grid_location: '1,0' }
  };
  G.goToStep(6);
  ['gen-lo-map', 'gen-pop-topo', 'gen-pop-wiring'].forEach(function (id) {
    ok(win.document.getElementById(id).open === true,
      'L5: #' + id + ' defaults open');
  });
  // A stored explicit collapse wins on the next entry.
  win.localStorage.setItem('quam_pop_wiring_open', '0');
  G.goToStep(5); G.goToStep(6);
  ok(win.document.getElementById('gen-pop-wiring').open === false,
    'L5: remembered collapse keeps the wiring mirror shut');
  ok(win.document.getElementById('gen-lo-map').open === true,
    'L5: the others stay open');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_step4_layout_selfcheck: all checks passed');
