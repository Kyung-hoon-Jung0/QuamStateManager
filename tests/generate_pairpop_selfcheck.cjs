/* jsdom behavioral check for the Generate-Config wizard's per-pair 2Q-gate
 * populate columns (feature B frontend). Asserts the populate step renders the
 * right columns for the chip's gate (CR vs CZ), that values flow to
 * spec.populate.pairs, and that CR pairs (which have no coupler line) still get
 * a populate table.
 *
 * Run:  node tests/generate_pairpop_selfcheck.cjs   (driven by test_generate_pairpop.py).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) {
  console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1);
});

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  return win;
}

function setInput(win, id, value) {
  const el = win.document.getElementById(id);
  if (!el) throw new Error('no #' + id);
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

// data-field set present on the rendered pair populate inputs.
function pairFields(win) {
  const host = win.document.getElementById('gen-pop-pairs');
  if (!host) return [];
  return Array.prototype.map.call(
    host.querySelectorAll('.gen-pop-in[data-field]'), function (el) { return el.dataset.field; });
}

// --- CR chip (fixed-frequency, cross_resonance, NO coupler line) -----------
(function crPairTable() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  // MW-only chip; pick the fixed-frequency (CR) architecture.
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'mw' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'fixed_frequency';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(G.state.pairGate === 'cr', 'CR: chip arch fixed_frequency -> pairGate cr (got ' + G.state.pairGate + ')');

  G.goToStep(6);
  const secPairs = win.document.getElementById('gen-pop-sec-pairs');
  ok(secPairs && !secPairs.hidden, 'CR: pairs populate section is SHOWN for a CR chip (no coupler line)');
  const fields = pairFields(win);
  ok(fields.indexOf('cr_drive_amplitude') >= 0, 'CR: cr_drive_amplitude column present (fields=' + fields.join(',') + ')');
  ok(fields.indexOf('qc_correction_phase') >= 0, 'CR: qc_correction_phase column present');
  ok(fields.indexOf('cz_amplitude') < 0, 'CR: no CZ columns on a CR chip');

  // Type a CR drive amp into the first pair row and confirm it reaches state.
  const host = win.document.getElementById('gen-pop-pairs');
  const drive = host.querySelector('.gen-pop-in[data-field="cr_drive_amplitude"]');
  ok(!!drive, 'CR: found the cr_drive_amplitude input');
  drive.value = '0.6';
  drive.dispatchEvent(new win.Event('change', { bubbles: true }));
  const pp = (G.state.spec.populate.pairs) || {};
  const key = Object.keys(pp)[0];
  ok(key && pp[key] && pp[key].cr_drive_amplitude === 0.6,
     'CR: cr_drive_amplitude written to populate.pairs (' + JSON.stringify(pp) + ')');
})();

// --- CZ tunable-coupler chip: cz_variant column + coupler column -----------
(function czPairTable() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'flux_tunable_coupler';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(G.state.pairGate === 'cz_tunable', 'CZ: arch -> cz_tunable (got ' + G.state.pairGate + ')');

  G.goToStep(6);
  const fields = pairFields(win);
  ok(fields.indexOf('cz_variant') >= 0, 'CZ: cz_variant column present (fields=' + fields.join(',') + ')');
  ok(fields.indexOf('coupler_interaction_offset') >= 0, 'CZ tunable: coupler column present');
  ok(fields.indexOf('cr_drive_amplitude') < 0, 'CZ: no CR columns on a CZ chip');

  // Select a cz_variant and confirm it reaches state.
  const host = win.document.getElementById('gen-pop-pairs');
  const sel = host.querySelector('select.gen-pop-in[data-field="cz_variant"]');
  ok(!!sel, 'CZ: found the cz_variant select');
  if (sel) {
    sel.value = 'SNZ';
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    const pp = (G.state.spec.populate.pairs) || {};
    const key = Object.keys(pp)[0];
    ok(key && pp[key] && pp[key].cz_variant === 'SNZ',
       'CZ: cz_variant=SNZ written to populate.pairs (' + JSON.stringify(pp) + ')');
  }
})();

// --- CZ fixed-coupler chip: pairs table shown, no coupler column -----------
(function czFixedPairTable() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  const arch = win.document.getElementById('gen-chip-arch');
  arch.value = 'flux_tunable_fixed_coupler';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(G.state.pairGate === 'cz_fixed', 'CZ-fixed: arch -> cz_fixed (got ' + G.state.pairGate + ')');
  G.goToStep(6);
  const secPairs = win.document.getElementById('gen-pop-sec-pairs');
  ok(secPairs && !secPairs.hidden, 'CZ-fixed: pairs section SHOWN (no coupler line, but pairs get CZ gates)');
  const fields = pairFields(win);
  ok(fields.indexOf('cz_variant') >= 0, 'CZ-fixed: cz_variant present');
  ok(fields.indexOf('coupler_interaction_offset') < 0, 'CZ-fixed: NO coupler column (fixed coupler)');
})();

// --- Arch switch prunes stale per-gate populate (CR cr_* -> CZ) -------------
(function archSwitchPrune() {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 2, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  const arch = win.document.getElementById('gen-chip-arch');
  // Enter CR, set a CR-only field, then switch to a CZ architecture.
  arch.value = 'fixed_frequency';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  G.goToStep(6);
  const host = win.document.getElementById('gen-pop-pairs');
  const drive = host && host.querySelector('.gen-pop-in[data-field="cr_drive_amplitude"]');
  if (!drive) { console.error('NOTE: archSwitchPrune skipped (no CR cell)'); return; }
  drive.value = '0.6';
  drive.dispatchEvent(new win.Event('change', { bubbles: true }));
  const pp = G.state.spec.populate.pairs || {};
  const key = Object.keys(pp)[0];
  ok(key && pp[key].cr_drive_amplitude === 0.6, 'prune: CR value set before switch');
  // Switch to a flux-tunable CZ chip — the stale cr_* keys must be pruned.
  G.goToStep(4);
  arch.value = 'flux_tunable_coupler';
  arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  const pp2 = G.state.spec.populate.pairs || {};
  const stale = Object.keys(pp2).some(function (id) {
    return pp2[id] && pp2[id].cr_drive_amplitude !== undefined;
  });
  ok(!stale, 'prune: stale cr_* keys removed after CR->CZ arch switch (' + JSON.stringify(pp2) + ')');
})();

if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
console.log('generate_pairpop_selfcheck: all checks passed');
