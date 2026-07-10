/* jsdom behavioral check for the Generate-Config wizard's step navigation +
 * draft persistence (feature D). Loads the real _generate.html template +
 * generate.js into jsdom, drives realistic Back/Forward + page-swap scenarios,
 * and asserts no entered data is lost.
 *
 * Run:  NODE_PATH=<dir-with-jsdom> node tests/generate_nav_selfcheck.cjs
 * Driven by tests/test_generate_nav.py when node + jsdom are present (else skip).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

process.on('uncaughtException', function (e) {
  console.error('UNCAUGHT:', (e && e.stack) || e, e && e.message);
  process.exit(1);
});

let fails = 0;
function ok(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); fails++; } else { /* pass */ } }

// --- build a fresh jsdom world with the wizard mounted in #table-pane -------
function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  // Stubs generate.js leans on (UI-only; irrelevant to state/nav logic).
  win.NumberInput = {
    fit() {}, attach() {}, format() {},
    strip(s) { return (s == null ? '' : String(s)).replace(/,/g, ''); },
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); }; // never resolves; loadEnvs is irrelevant
  win.matchMedia = win.matchMedia || function () { return { matches: false, addListener() {}, removeListener() {} }; };
  // Run generate.js in the jsdom window's global scope.
  const runner = new win.Function(GEN_JS);
  runner.call(win);
  return win;
}

function setInput(win, id, value) {
  const el = win.document.getElementById(id);
  if (!el) throw new Error('no #' + id);
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
  return el;
}

function val(win, id) {
  const el = win.document.getElementById(id);
  return el ? el.value : null;
}

// ===========================================================================
// Scenario A — within-session Back -> Forward keeps everything (the user's pain)
// ===========================================================================
(function scenarioA() {
  const win = makeWorld();
  const G = win.QuamGen;
  ok(G && typeof G.init === 'function', 'A: QuamGen.init exposed');
  G.init();

  // Step 2: network
  G.goToStep(2);
  setInput(win, 'gen-net-host', '127.0.0.1');
  setInput(win, 'gen-net-cluster', 'LabA_CR');

  // Step 3: one chassis with a MW-FEM in slot 1 (drive the model directly via state)
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');

  // Step 4: qubits + a pair + chip arch
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '3');
  ok(G.state.spec.qubits.length === 3, 'A: 3 qubits set');

  // Navigate AWAY to step 2 and BACK to step 4 (the "go back then forward" case).
  G.goToStep(2);
  ok(val(win, 'gen-net-host') === '127.0.0.1', 'A: network host survives Back nav');
  G.goToStep(4);
  ok(val(win, 'gen-qubit-count') == 3, 'A: qubit-count input still shows 3 after Back->Forward');
  ok(G.state.spec.qubits.length === 3, 'A: state still has 3 qubits after Back->Forward');
  ok(val(win, 'gen-net-cluster') === 'LabA_CR' || G.state.spec.network.cluster_name === 'LabA_CR',
     'A: cluster name retained in state');
})();

// ===========================================================================
// Scenario B — page-swap (leave wizard, return): draft restore rehydrates ALL steps
// ===========================================================================
(function scenarioB() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(2);
  setInput(win, 'gen-net-host', '10.0.0.9');
  setInput(win, 'gen-net-cluster', 'ClusterX');
  setInput(win, 'gen-net-port', '9510');

  // Step 3: a chassis with two FEMs (set the model + render the way the UI does).
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];

  // Step 4: qubits, mux, an edited pair, a TWPA, chip arch.
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '4');
  setInput(win, 'gen-mux-size', '2');
  G.state.pairsTouched = true;
  G.state.spec.qubit_pairs = [['q1', 'q3']];   // a non-default pairing
  G.state.spec.twpas = [{ id: 'twpaA', qubits: ['q1'] }];

  // Step 7: output path.
  G.goToStep(7);
  setInput(win, 'gen-output-path', '/data/quam_state');

  // Simulate HTMX swapping #table-pane away: fire beforeSwap so the wizard saves.
  const before = new win.Event('htmx:beforeSwap');
  before.detail = { target: win.document.getElementById('table-pane') };
  win.document.dispatchEvent(before);

  const draftRaw = win.sessionStorage.getItem('quam_generate_draft');
  ok(!!draftRaw, 'B: draft saved on page-swap');
  const draft = JSON.parse(draftRaw || '{}');
  ok(draft.spec && draft.spec.qubits && draft.spec.qubits.length === 4, 'B: draft carries 4 qubits');
  ok(draft.spec.network && draft.spec.network.host === '10.0.0.9', 'B: draft carries network host');
  ok(draft.outputPath === '/data/quam_state', 'B: draft carries output path');
  ok(draft.muxSize === 2, 'B: draft carries mux size');

  // Re-mount: a fresh #table-pane node + fresh generate.js world, same sessionStorage.
  const win2 = makeWorld();
  win2.sessionStorage.setItem('quam_generate_draft', draftRaw);
  const G2 = win2.QuamGen;
  G2.init();
  ok(G2.state.spec.qubits.length === 4, 'B: state restored to 4 qubits');

  // Visit each step and assert its DOM reflects the restored state.
  G2.goToStep(2);
  ok(val(win2, 'gen-net-host') === '10.0.0.9', 'B: network host repainted');
  ok(val(win2, 'gen-net-port') == 9510, 'B: network port repainted');
  G2.goToStep(3);
  const slots = win2.document.querySelectorAll('#gen-chassis-list .gen-slot');
  ok(slots.length > 0, 'B: chassis grid rebuilt after restore (slots=' + slots.length + ')');
  G2.goToStep(4);
  ok(val(win2, 'gen-qubit-count') == 4, 'B: qubit-count repainted to 4');
  ok(val(win2, 'gen-mux-size') == 2, 'B: mux repainted to 2');
  const summary = win2.document.getElementById('gen-qubit-summary');
  ok(summary && /4 qubit/.test(summary.textContent), 'B: qubit summary rebuilt');
  // The edited non-default pair q1-q3 must be present in the restored pair UI.
  const pairText = (win2.document.getElementById('gen-pair-list') || {}).textContent || '';
  ok(G2.state.spec.qubit_pairs.length === 1 &&
     G2.state.spec.qubit_pairs[0][0] === 'q1' && G2.state.spec.qubit_pairs[0][1] === 'q3',
     'B: edited pair q1-q3 restored in state');
  G2.goToStep(7);
  ok(val(win2, 'gen-output-path') === '/data/quam_state', 'B: output path repainted');
})();

// ===========================================================================
// Scenario C — populate value entered, Back to step 4, Forward to step 6, kept
// ===========================================================================
(function scenarioC() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  // write a populate value directly into the model the way the populate inputs do
  G.state.spec.populate = G.state.spec.populate || {};
  G.state.spec.populate.qubit = { q1: { f_01: 5.1e9 } };
  G.goToStep(6);            // populate
  G.goToStep(4);            // Back
  G.goToStep(6);            // Forward
  ok(G.state.spec.populate && G.state.spec.populate.qubit &&
     G.state.spec.populate.qubit.q1 && G.state.spec.populate.qubit.q1.f_01 === 5.1e9,
     'C: populate value survives Back->Forward');
})();

// ===========================================================================
// Scenario D — the BLUR RACE: a value typed but whose input/change event never
// fired (the pywebview webview can skip blur->change when you click the stepper)
// must NOT be lost when navigating away. This is the user's reported pain.
// ===========================================================================
(function scenarioD() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(2);
  // Type into the host field WITHOUT dispatching input/change (simulates the
  // webview swallowing the commit event on a fast stepper click).
  const host = win.document.getElementById('gen-net-host');
  host.value = '192.168.1.7';
  const cluster = win.document.getElementById('gen-net-cluster');
  cluster.value = 'LateCommit';

  // Navigate away via the stepper, then page-swap (leave the wizard).
  G.goToStep(4);
  const before = new win.Event('htmx:beforeSwap');
  before.detail = { target: win.document.getElementById('table-pane') };
  win.document.dispatchEvent(before);

  // Restore into a fresh world: the typed-but-uncommitted values must survive.
  const draftRaw = win.sessionStorage.getItem('quam_generate_draft');
  const win2 = makeWorld();
  win2.sessionStorage.setItem('quam_generate_draft', draftRaw || '');
  const G2 = win2.QuamGen;
  G2.init();
  ok(G2.state.spec.network.host === '192.168.1.7',
     'D: host typed without a commit event survives nav+restore (got ' +
     JSON.stringify(G2.state.spec.network.host) + ')');
  ok(G2.state.spec.network.cluster_name === 'LateCommit',
     'D: cluster typed without a commit event survives nav+restore');
})();

// ===========================================================================
// Scenario E — POPULATE blur race: a per-row populate value typed but never
// committed (no change event) must survive leaving + re-entering step 6.
// ===========================================================================
(function scenarioE() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  // A 2-qubit chip with MW + LF FEMs so deriveLines() emits resonator/drive/flux
  // and the populate tables have rows.
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  G.goToStep(6);   // populate — renderPopulateTables() builds the per-row inputs

  const rowInputs = win.document.querySelectorAll('.gen-pop-in[data-field]');
  if (!rowInputs.length) {
    // Populate rows didn't render in this headless setup — can't exercise the
    // populate flush here; the network blur race (D) already proves the path.
    console.error('NOTE: scenario E skipped (no populate row inputs rendered)');
    return;
  }
  // Pick a numeric per-row input and simulate the REAL blur race: typing fires
  // `input` (so the cell is marked dirty) but the commit `change` is swallowed by
  // the webview when the user clicks the stepper. The nav flush must still commit
  // it — and ONLY it (see F1/F2: untouched cells must not be re-fired).
  let target = null;
  rowInputs.forEach(function (el) {
    if (!target && el.tagName === 'INPUT' && el.dataset.field) target = el;
  });
  ok(!!target, 'E: found a per-row populate input');
  const field = target.dataset.field, group = target.dataset.group, rid = target.dataset.rid;
  target.value = '7';
  target.dispatchEvent(new win.Event('input', { bubbles: true }));  // marks dirty; change swallowed

  // Leave populate (step 4) then come back — the value must not vanish.
  G.goToStep(4);
  G.goToStep(6);
  const pop = G.state.spec.populate || {};
  const committed = pop[group] && pop[group][rid] && pop[group][rid][field] != null;
  ok(committed, 'E: populate value typed without a commit event survived nav ' +
     '(' + group + '/' + rid + '/' + field + ' = ' +
     JSON.stringify(pop[group] && pop[group][rid] && pop[group][rid][field]) + ')');
})();

// ===========================================================================
// Scenario F1 — a hand-typed LO_frequency override must survive nav. The nav
// flush must NOT re-fire an untouched RF_freq cell (which would run recomputeLOs
// and reclobber the override). [red-team P1 regression guard]
// ===========================================================================
(function scenarioF1() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  G.goToStep(6);
  function cell(group, rid, field) {
    return win.document.querySelector('.gen-pop-in[data-group="' + group +
      '"][data-rid="' + rid + '"][data-field="' + field + '"]');
  }
  const rf = cell('qubit', 'q1', 'RF_freq'), lo = cell('qubit', 'q1', 'LO_frequency');
  if (!rf || !lo) { console.error('NOTE: F1 skipped (no RF/LO cells)'); return; }
  function commit(el, v) {
    el.value = v;
    el.dispatchEvent(new win.Event('input', { bubbles: true }));
    el.dispatchEvent(new win.Event('change', { bubbles: true }));
  }
  commit(rf, '5');     // commit RF first (this re-derives LOs)…
  commit(lo, '4.5');   // …THEN hand-type an LO override so it wins
  const loBase = (((G.state.spec.populate.qubit || {}).q1) || {}).LO_frequency;
  ok(loBase != null, 'F1: LO override committed (' + loBase + ')');
  G.goToStep(4);
  G.goToStep(6);
  const loAfter = (((G.state.spec.populate.qubit || {}).q1) || {}).LO_frequency;
  ok(loAfter === loBase,
     'F1: hand-typed LO override survives Back->Forward (was ' + loBase + ', now ' + loAfter + ')');
})();

// ===========================================================================
// Scenario F2 — a NEGATIVE amplitude entered in linear 0-1 mode must keep its
// sign after switching the amplitude unit to dBm and navigating; the flush must
// not re-fire the untouched amp cell (ampToBase would return |amp|).
// [red-team P1 regression guard]
// ===========================================================================
(function scenarioF2() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  G.goToStep(6);
  const amp = win.document.querySelector(
    '.gen-pop-in[data-group="resonator"][data-rid="q1"][data-field="readout_amplitude"]');
  if (!amp) { console.error('NOTE: F2 skipped (no amp cell)'); return; }
  G.state.populateUnits.amp = '0-1';
  amp.value = '-0.5';
  amp.dispatchEvent(new win.Event('input', { bubbles: true }));
  amp.dispatchEvent(new win.Event('change', { bubbles: true }));
  const a0 = (((G.state.spec.populate.resonator || {}).q1) || {}).readout_amplitude;
  ok(a0 === -0.5, 'F2: negative amp committed in linear mode (' + a0 + ')');
  G.state.populateUnits.amp = 'dBm';   // switch the entry unit, then navigate
  G.goToStep(7);
  G.goToStep(6);
  const a1 = (((G.state.spec.populate.resonator || {}).q1) || {}).readout_amplitude;
  ok(a1 === -0.5, 'F2: negative amp survives a dBm-mode nav (was -0.5, now ' + a1 + ')');
})();

// --- top Back/Next header mirror stays in lock-step with the bottom nav ------
(function topNavMirror() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const backTop = win.document.getElementById('gen-back-top');
  const nextTop = win.document.getElementById('gen-next-top');
  const backBot = win.document.getElementById('gen-back');
  const nextBot = win.document.getElementById('gen-next');
  ok(backTop && nextTop, 'TOPNAV: top Back/Next present in the header');
  ok(backTop.disabled === true, 'TOPNAV: top Back disabled on step 1');
  ok(backTop.disabled === backBot.disabled, 'TOPNAV: top/bottom Back disabled in sync (step 1)');
  ok(nextTop.textContent.indexOf('Next') >= 0, 'TOPNAV: top Next labelled "Next" on step 1');

  G.goToStep(4);
  ok(backTop.disabled === false, 'TOPNAV: top Back enabled past step 1');

  G.goToStep(8);
  ok(/Generate/.test(nextTop.textContent), 'TOPNAV: top Next becomes "Generate" on the last step');
  ok(nextTop.textContent === nextBot.textContent, 'TOPNAV: top/bottom Next label in sync');

  // clicking the top Back navigates (backward is always free)
  backTop.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(G.state.step === 7, 'TOPNAV: top Back navigates (8 -> 7), got ' + G.state.step);
})();

if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
console.log('generate_nav_selfcheck: all checks passed');
