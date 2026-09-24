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
  // The readout multiplex bound is 8 per feedline — typing past it clamps.
  setInput(win, 'gen-mux-size', '16');
  ok(G.state.muxSize === 8, 'L4: mux clamps to the 8-per-feedline bound (got ' +
    G.state.muxSize + ')');
  ok(win.document.getElementById('gen-mux-size').value == 8,
    'L4: the input snaps back to 8');
  ok(win.document.getElementById('gen-mux-size').max === '8',
    'L4: input max attribute is 8');
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

// L6: architecture radios — all options visible, one-click switching, the
// hidden #gen-chip-arch select stays the state anchor, hardware-impossible
// options render disabled.
(function archRadios() {
  const win = makeWorld();
  const G = buildTo4(win);
  const radios = win.document.querySelectorAll('#gen-arch-radios input[type="radio"]');
  ok(radios.length === 3, 'L6: three architecture radios rendered');
  ok(win.document.getElementById('gen-chip-arch').hidden === true,
    'L6: the select anchor is hidden');
  const checked = function () {
    return win.document.querySelector('#gen-arch-radios input:checked');
  };
  ok(checked() && checked().value === 'flux_tunable_coupler',
    'L6: default arch radio checked');
  // One click switches the architecture through the anchor.
  const ff = win.document.querySelector(
    '#gen-arch-radios input[value="fixed_frequency"]');
  ff.checked = true;
  ff.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(G.state.chipArch === 'fixed_frequency' && G.state.pairGate === 'cr',
    'L6: radio click drives applyChipArch (got ' + G.state.chipArch + ')');
  ok(checked() && checked().value === 'fixed_frequency',
    'L6: re-rendered radios mirror the new state');
  // Driving the anchor directly (the selfcheck idiom) re-syncs the radios.
  const sel = win.document.getElementById('gen-chip-arch');
  sel.value = 'flux_tunable_fixed_coupler';
  sel.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(checked() && checked().value === 'flux_tunable_fixed_coupler',
    'L6: anchor-driven change mirrors back into the radios');
  // MW-only hardware: the two flux-tunable options render disabled.
  const win2 = makeWorld();
  buildTo4(win2, { mwOnly: true });
  const off = win2.document.querySelectorAll('#gen-arch-radios input:disabled');
  ok(off.length === 2, 'L6: flux-tunable radios disabled without an LF-FEM (got ' +
    off.length + ')');
})();

// L6 (QA F15): the board caption and the rename list follow a pair / qubit
// edit made OUTSIDE the board at once. jsdom's .click() fires no mouseup, so
// the document-level mouseup repaint that used to catch up one action later
// cannot mask a missing refresh here.
(function captionFollowsEdits() {
  const win = makeWorld();
  const G = buildTo4(win);
  setInput(win, 'gen-qubit-count', '4');
  const cap = win.document.getElementById('gen-topo-caption');
  const capPairs = function () {
    const m = /(\d+) pairs?\b/.exec(cap.textContent);
    return m ? +m[1] : null;
  };
  ok(capPairs() === G.state.spec.qubit_pairs.length,
    'L6: caption starts in step (got: ' + cap.textContent + ')');
  win.document.getElementById('gen-add-pair').click();
  ok(capPairs() === G.state.spec.qubit_pairs.length,
    'L6: + Add pair updates the caption at once (caption ' + capPairs() +
    ', pairs ' + G.state.spec.qubit_pairs.length + ')');
  const dels = win.document.querySelectorAll('#gen-pair-list .gen-row-del');
  dels[dels.length - 1].click();
  ok(capPairs() === G.state.spec.qubit_pairs.length,
    'L6: a row x updates the caption at once (caption ' + capPairs() +
    ', pairs ' + G.state.spec.qubit_pairs.length + ')');
  // a Control/Target change moves the edge: the board repaints
  let refreshed = 0;
  const realRefresh = win.WiringGrid.refresh;
  win.WiringGrid.refresh = function () { refreshed++; return realRefresh.apply(this, arguments); };
  const tSel = win.document.querySelector('#gen-pair-list .gen-pair-t');
  tSel.value = 'q4';
  tSel.dispatchEvent(new win.Event('change', { bubbles: true }));
  win.WiringGrid.refresh = realRefresh;
  ok(refreshed > 0, 'L6: a Target change repaints the board');
  // a board delete repaints the rename list
  win.WiringGrid._removeQubit('q3');
  const names = Array.prototype.map.call(
    win.document.querySelectorAll('#gen-qubit-name-list input'),
    function (i) { return i.value; });
  ok(names.join(',') === G.state.spec.qubits.join(','),
    'L6: the rename list follows a board delete (list ' + names.join(',') +
    ', qubits ' + G.state.spec.qubits.join(',') + ')');
  // one pair reads "1 pair", never "1 pairs"
  while (G.state.spec.qubit_pairs.length > 1) {
    win.document.querySelector('#gen-pair-list .gen-row-del').click();
  }
  ok(/ 1 pair$/.test(cap.textContent), 'L6: singular caption (got: ' + cap.textContent + ')');
})();

// L7 (QA regenerate-r2-34): + Add pair pre-fills a pair not listed yet, and
// a pair listed twice is refused at the step-4 gate (ordered for CR, where
// q1->q2 and q2->q1 are two drives; unordered for CZ, where they are one).
(function noDuplicatePairs() {
  const win = makeWorld();
  const G = buildTo4(win);                       // CZ tunable
  setInput(win, 'gen-qubit-count', '3');         // chain: q1-q2, q2-q3
  const key = function (p) { return p.slice().sort().join('|'); };
  const addBtn = win.document.getElementById('gen-add-pair');
  addBtn.click();
  const added = G.state.spec.qubit_pairs[G.state.spec.qubit_pairs.length - 1];
  ok(JSON.stringify(added) === '["q1","q3"]',
    'L7: the added pair is the first one not listed — got ' + JSON.stringify(added));
  addBtn.click();                                // every combination is taken
  const blank = G.state.spec.qubit_pairs[G.state.spec.qubit_pairs.length - 1];
  ok(!blank[0] && !blank[1], 'L7: blank only when every pair is listed — got ' +
    JSON.stringify(blank));
  const T = win.QuamGen._test;
  ok(JSON.stringify(T.nextFreePair(['q1', 'q2', 'q3', 'q4'], [['q2', 'q1']])) === '["q2","q3"]',
    'L7: a reversed pair counts as listed — got ' +
    JSON.stringify(T.nextFreePair(['q1', 'q2', 'q3', 'q4'], [['q2', 'q1']])));
  // the gate, through the real Next: CZ -- q1-q2 and q2-q1 are one pair
  const next = function (w, g) {
    w.document.getElementById('gen-next').click();
    const m = w.document.getElementById('gen-message');
    return { step: g.state.step, msg: m.hidden ? '' : m.textContent };
  };
  G.state.spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q1']];
  let r = next(win, G);
  ok(r.step === 4 && /q2–q1 is listed twice/.test(r.msg),
    'L7: a CZ pair listed in both orders is refused — got ' + JSON.stringify(r));
  // CR: anti-parallel pairs are two drives and pass; an exact repeat does not
  const win2 = makeWorld();
  const G2 = buildTo4(win2, { mwOnly: true, arch: 'fixed_frequency' });
  setInput(win2, 'gen-qubit-count', '3');
  G2.state.spec.qubit_pairs = [['q1', 'q2'], ['q1', 'q2']];
  r = next(win2, G2);
  ok(r.step === 4 && /q1→q2 is listed twice/.test(r.msg),
    'L7: an exact CR repeat is refused — got ' + JSON.stringify(r));
  G2.state.spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q1']];
  r = next(win2, G2);
  ok(!/listed twice/.test(r.msg),
    'L7: anti-parallel CR pairs are not called duplicates — got ' + JSON.stringify(r));
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_step4_layout_selfcheck: all checks passed');
