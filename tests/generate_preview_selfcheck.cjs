/* jsdom behavioral check for the Generate-Config wizard's live waveform preview
 * (feature C, generate_preview.js). Verifies the (populate group, row) -> (qclass,
 * params) mapping and that focusing/editing a previewable cell POSTs the right
 * {qclass, params} to /api/pulse/synth and renders via PulsesPage.renderPulsePlot.
 *
 * Run:  node tests/generate_preview_selfcheck.cjs   (driven by test_generate_preview.py).
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) { console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1); });

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
const GEN = fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/static/generate.js'), 'utf8');
const PREV = fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/static/generate_preview.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

let lastFetch = null, renderCalls = [];

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="table-pane">' + HTML + '</div></body>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  // Capture synth POSTs; resolve with a fake-but-valid plot.
  win.fetch = function (url, opts) {
    lastFetch = { url: url, body: JSON.parse((opts && opts.body) || '{}') };
    return win.Promise.resolve({ json: function () {
      return win.Promise.resolve({ ok: true, error: null, param_errors: {}, plot: { ok: true, traces: [] } });
    } });
  };
  win.PulsesPage = { renderPulsePlot: function (divId, plot) { renderCalls.push({ divId: divId, plot: plot }); } };
  new win.Function(GEN).call(win);
  new win.Function(PREV).call(win);
  return win;
}

function setInput(win, id, value) {
  const el = win.document.getElementById(id);
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

function chipTo6(win, arch, fems) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = fems;
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '2');
  const a = win.document.getElementById('gen-chip-arch');
  a.value = arch; a.dispatchEvent(new win.Event('change', { bubbles: true }));
  G.goToStep(6);
  return G;
}

const sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };

(async function main() {
  // ---- describe() mapping ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    const GP = win.GenPreview;
    ok(GP && typeof GP.describe === 'function', 'C: GenPreview.describe exposed');

    // 1Q DRAG, pulling anharmonicity from the qubit row.
    G.state.spec.populate.qubit = { q1: { anharmonicity: -2.1e8 } };
    G.state.spec.populate.pulses = { q1: { x180_length: 48, x180_amplitude: 0.25, drag_alpha: 0.3 } };
    let d = GP.describe('pulses', 'q1');
    ok(d && d.qclass === 'DragCosinePulse', 'C: pulses -> DragCosinePulse');
    ok(d && d.params.length === 48 && d.params.amplitude === 0.25 && d.params.alpha === 0.3, 'C: DRAG params mapped');
    ok(d && d.params.anharmonicity === -2.1e8, 'C: DRAG anharmonicity from the qubit row');

    // readout
    G.state.spec.populate.resonator = { q1: { readout_length: 2500, readout_amplitude: 0.12 } };
    d = GP.describe('resonator', 'q1');
    ok(d && d.qclass === 'SquareReadoutPulse' && d.params.length === 2500 && d.params.amplitude === 0.12, 'C: resonator -> SquareReadoutPulse');

    // not-a-pulse rows
    ok(GP.describe('qubit', 'q1') === null, 'C: qubit-frequency row -> no preview');
    ok(GP.describe('flux', 'q1') === null, 'C: flux row -> no preview');

    // CZ variant mapping
    G.state.spec.populate.pairs = { 'q1-q2': { cz_variant: 'SNZ', cz_interaction_duration: 80, cz_amplitude: 0.2 } };
    d = GP.describe('pairs', 'q1-q2');
    ok(d && d.qclass === 'SNZPulse' && d.params.flat_length === 80 && d.params.amplitude === 0.2, 'C: pairs CZ SNZ -> SNZPulse');
    G.state.spec.populate.pairs['q1-q2'].cz_variant = 'flattop';
    ok(GP.describe('pairs', 'q1-q2').qclass === '_FlatTopGaussianPulse', 'C: CZ flattop -> _FlatTopGaussianPulse');
    G.state.spec.populate.pairs['q1-q2'].cz_variant = '';
    ok(GP.describe('pairs', 'q1-q2').qclass === 'SquarePulse', 'C: CZ default (unipolar) -> SquarePulse');
  }

  // ---- CR pair preview ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'fixed_frequency', [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'mw' }]);
    G.state.spec.populate.pairs = { 'q1-q2': { cr_drive_amplitude: 0.6 } };
    const d = win.GenPreview.describe('pairs', 'q1-q2', 'cr_drive_amplitude');
    ok(d && d.qclass === 'SquarePulse' && d.params.amplitude === 0.6, 'C: CR pair -> SquarePulse drive (amp 0.6)');
    ok(/CR.*drive/.test(d.title), 'C: CR drive title');
    // Editing the cancel-amplitude column previews the cancel tone, not the drive.
    win.GenPreview && (G.state.spec.populate.pairs['q1-q2'].cr_cancel_amplitude = 0.15);
    const dc = win.GenPreview.describe('pairs', 'q1-q2', 'cr_cancel_amplitude');
    ok(dc && dc.params.amplitude === 0.15 && /cancel/.test(dc.title), 'C: CR cancel field -> cancel tone preview');
  }

  // ---- focusin drives a synth POST + renders ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    G.state.spec.populate.pulses = { q1: { x180_length: 40, x180_amplitude: 0.2 } };
    lastFetch = null; renderCalls = [];
    const cell = win.document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    if (!cell) { console.error('NOTE: focusin preview skipped (no pulses cell rendered)'); }
    else {
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);   // past the 150ms debounce
      ok(lastFetch && lastFetch.url === '/api/pulse/synth', 'C: focus a pulse cell POSTs /api/pulse/synth');
      ok(lastFetch && lastFetch.body.qclass === 'DragCosinePulse', 'C: synth body carries qclass DragCosinePulse');
      ok(renderCalls.length >= 1 && renderCalls[0].divId === 'gen-pop-preview-plot', 'C: renders into the preview panel');
      const panel = win.document.getElementById('gen-pop-preview');
      ok(panel && !panel.hidden, 'C: preview panel shown');
    }
  }

  // ---- P1: focus a non-previewable cell hides the (sticky) panel ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    G.state.spec.populate.pulses = { q1: { x180_amplitude: 0.2 } };
    const panel = win.document.getElementById('gen-pop-preview');
    const pulseCell = win.document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    const freqCell = win.document.querySelector('.gen-pop-in[data-group="qubit"][data-rid="q1"][data-field="RF_freq"]');
    if (pulseCell && freqCell) {
      pulseCell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);
      ok(!panel.hidden, 'C P1: panel shown after focusing a pulse cell');
      freqCell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      ok(panel.hidden, 'C P1: panel HIDDEN after focusing a non-previewable (freq) cell');
    } else { console.error('NOTE: P1 skipped (cells missing)'); }
  }

  // ---- P2: typing (input, no blur/change) updates the preview LIVE ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    const cell = win.document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_length"]');
    if (cell) {
      lastFetch = null;
      cell.value = '88';
      cell.dispatchEvent(new win.Event('input', { bubbles: true }));   // NO change
      await sleep(220);
      ok(lastFetch && lastFetch.body.params && lastFetch.body.params.length === 88,
         'C P2: input-only edit previews the typed value (live), got ' +
         JSON.stringify(lastFetch && lastFetch.body.params && lastFetch.body.params.length));
    } else { console.error('NOTE: P2 skipped (no x180_length cell)'); }
  }

  // ---- reset() clears the panel (re-entering step 6 drops a stale preview) ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    const cell = win.document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    const panel = win.document.getElementById('gen-pop-preview');
    if (cell) {
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);
      ok(!panel.hidden, 'C reset: panel shown before reset');
      win.GenPreview.reset();
      ok(panel.hidden, 'C reset: panel hidden after reset()');
      ok(panel.querySelector('.gen-pop-preview-title').textContent === '', 'C reset: title cleared');
    } else { console.error('NOTE: reset skipped'); }
  }

  // ---- on/off toggle: the × and the checkbox suppress the preview persistently ----
  {
    const win = makeWorld();
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    G.state.spec.populate.pulses = { q1: { x180_amplitude: 0.2 } };
    const panel = win.document.getElementById('gen-pop-preview');
    const toggle = win.document.getElementById('gen-preview-toggle');
    const closeBtn = win.document.getElementById('gen-pop-preview-close');
    const cell = win.document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    ok(toggle && closeBtn, 'toggle: checkbox + close button present');
    if (cell && toggle && closeBtn) {
      // shows by default
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);
      ok(!panel.hidden, 'toggle: preview shows by default');

      // × turns it OFF: panel hides, pref persists, checkbox unticks
      closeBtn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
      ok(panel.hidden, 'toggle: × hides the panel');
      ok(win.localStorage.getItem('quam_gen_preview_off') === '1', 'toggle: × persists the off pref');
      ok(toggle.checked === false, 'toggle: × unchecks the toggle');

      // while OFF, focusing a pulse cell does NOT re-show it
      lastFetch = null;
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);
      ok(panel.hidden, 'toggle: stays hidden on focus while off');
      ok(lastFetch === null, 'toggle: no synth POST fired while off');

      // re-checking the toggle re-enables it
      toggle.checked = true;
      toggle.dispatchEvent(new win.Event('change', { bubbles: true }));
      ok(win.localStorage.getItem('quam_gen_preview_off') === '0', 'toggle: re-check clears the off pref');
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);
      ok(!panel.hidden, 'toggle: preview shows again after re-enabling');
    }
  }

  // ---- the off pref survives a fresh entry (reset syncs the checkbox) ----
  {
    const win = makeWorld();
    win.localStorage.setItem('quam_gen_preview_off', '1');     // user turned it off earlier
    const G = chipTo6(win, 'flux_tunable_coupler', [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }]);
    const toggle = win.document.getElementById('gen-preview-toggle');
    const panel = win.document.getElementById('gen-pop-preview');
    win.GenPreview.reset();
    ok(toggle && toggle.checked === false, 'toggle: persisted off reflects in the checkbox on entry');
    const cell = win.document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    if (cell) {
      lastFetch = null;
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(220);
      ok(panel.hidden && lastFetch === null, 'toggle: stays off across a fresh populate entry');
    }
  }

  if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
  console.log('generate_preview_selfcheck: all checks passed');
})();
