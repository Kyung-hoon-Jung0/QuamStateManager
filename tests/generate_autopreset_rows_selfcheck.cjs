/* jsdom behavioral check: the Populate step's standard-defaults prefill runs
 * once per ROW, not once per draft (QA generate-r2-05).
 *
 * The bug: autoApplyStandardDefaults was one-shot per draft, so a qubit or
 * pair added after the first Populate visit (5 -> 3 -> 5, 20 -> 200) stayed
 * blank, and the build filled it with the builder's own, different defaults
 * (x180 amp 0.1 vs 0.25, readout 2000 vs 1000 ns, ToF 32 vs 28 ...) — a
 * silently non-uniform chip.
 *
 * Pins:
 *  A1. the first visit prefills every row (qubits + chain pairs).
 *  A2. 5 -> 3 -> 5: the re-created q4/q5 and their pairs prefill on the next
 *      visit; a cell the user cleared on q1 STAYS cleared.
 *  A3. a rename (applyQubitIdMap) is not a new row — no refill, no fetch.
 *  A4. an old draft whose one-shot flag was spent: its rows count as
 *      prefilled, only rows added afterwards prefill. No flag: all prefill.
 *  A5. Start over (resetWizard) lets a fresh chip prefill again.
 *  A6. an explicit Apply (no row filter) still reaches every row.
 *
 * Run:  node tests/generate_autopreset_rows_selfcheck.cjs
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

// A representative slice of the builtin "Standard defaults" preset.
const PRESET = { ok: true, sections: {
  pulses: { defaults: { x180_amplitude: 0.25, x180_length: 40 } },
  resonator: { defaults: { readout_length: 1000, time_of_flight: 28,
                           depletion_time: 10000 } },
  // The harness chip (MW-FEM only) is a CR chip; a CZ chip reads cz_amplitude.
  pairs: { defaults: { cz_amplitude: 0.1, cr_drive_amplitude: 0.1 } }
} };

function makeWorld(opts) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win._fetchCount = 0;
  win.fetch = function () {
    win._fetchCount++;
    return win.Promise.resolve({ json: function () {
      return win.Promise.resolve(JSON.parse(JSON.stringify(PRESET))); } });
  };
  new win.Function(GEN_JS).call(win);
  if (opts && opts.board) {         // the step-4 chip board (removeQubit)
    ['topo-graph.js', 'wiring-grid.js'].forEach(function (n) {
      new win.Function(fs.readFileSync(path.join(
        ROOT, 'quam_state_manager', 'web', 'static', n), 'utf8')).call(win);
    });
  }
  const G = win.QuamGen;
  G.init();
  G.state.spec.instruments.controllers = [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }];
  return win;
}
function setCount(win, n) {
  const el = win.document.getElementById('gen-qubit-count');
  el.value = String(n);
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }
async function visit(win) { win.QuamGen._test.autoApplyStandardDefaults(); await tick(); }
function pop(win) { return win.QuamGen.state.spec.populate; }
function x180(win, q) { return ((pop(win).pulses || {})[q] || {}).x180_amplitude; }
function czAmp(win, id) {
  const b = (pop(win).pairs || {})[id] || {};
  return b.cz_amplitude != null ? b.cz_amplitude : b.cr_drive_amplitude;
}

(async function () {
  // ---- A1 + A2 + A3 ---------------------------------------------------------
  {
    const win = makeWorld();
    const st = win.QuamGen.state;
    setCount(win, 5);
    ok(st.spec.qubits.join(',') === 'q1,q2,q3,q4,q5', 'setup: 5 qubits');
    await visit(win);
    ok(['q1', 'q2', 'q3', 'q4', 'q5'].every(function (q) { return x180(win, q) === 0.25; }),
       'A1: the first visit prefills every qubit');
    ok(czAmp(win, 'q1-q2') === 0.1 && czAmp(win, 'q4-q5') === 0.1,
       'A1: ...and every chain pair');

    delete pop(win).pulses.q1.x180_amplitude;          // the user clears one cell
    setCount(win, 3);
    setCount(win, 5);
    ok(x180(win, 'q4') === undefined, 'setup: re-created q4 starts blank');
    await visit(win);
    ok(x180(win, 'q4') === 0.25 && x180(win, 'q5') === 0.25,
       'A2: q4/q5 re-created after the first visit get the standard x180 amp');
    ok(((pop(win).resonator || {}).q5 || {}).readout_length === 1000 &&
       ((pop(win).resonator || {}).q5 || {}).time_of_flight === 28,
       'A2: ...and the standard readout length / ToF');
    ok(czAmp(win, 'q3-q4') === 0.1 && czAmp(win, 'q4-q5') === 0.1,
       'A2: ...and their re-created pairs');
    ok(x180(win, 'q1') === undefined, 'A2: a cell cleared on q1 stays cleared');
    const note = win.document.getElementById('gen-preset-note');
    ok(note && /q4/.test(note.textContent) && /q5/.test(note.textContent),
       'A2: the note names the newly prefilled rows');

    delete pop(win).pulses.q5.x180_amplitude;          // cleared, then renamed
    const before = win._fetchCount;
    win.QuamGen._test.applyQubitIdMap({ q5: 'q6' });
    ok(st.spec.qubits.indexOf('q6') >= 0, 'setup: q5 renamed to q6');
    await visit(win);
    ok(x180(win, 'q6') === undefined, 'A3: a renamed row is not refilled');
    ok(win._fetchCount === before, 'A3: nothing fresh -> no fetch at all');
  }

  // ---- A4: draft migration -----------------------------------------------------
  {
    const win = makeWorld();
    const T = win.QuamGen._test;
    const spec = {
      network: { host: '', cluster_name: '' },
      instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                     opx_plus: [], octaves: [] },
      qubits: ['q1', 'q2', 'q3'], qubit_pairs: [['q1', 'q2'], ['q2', 'q3']],
      twpas: [], lines: [], populate: {}
    };
    T.applyDraft({ spec: JSON.parse(JSON.stringify(spec)), step: 6,
                   autoPresetApplied: true, pairsTouched: true });
    const st = win.QuamGen.state;
    st.spec.qubits.push('q4');
    st.spec.qubit_pairs.push(['q3', 'q4']);
    await visit(win);
    ok(x180(win, 'q4') === 0.25 && czAmp(win, 'q3-q4') === 0.1,
       'A4: a spent old draft prefills the rows added afterwards');
    ok(x180(win, 'q1') === undefined && czAmp(win, 'q1-q2') === undefined,
       'A4: ...and leaves its existing rows as they were');

    const win2 = makeWorld();
    win2.QuamGen._test.applyDraft({ spec: JSON.parse(JSON.stringify(spec)), step: 6,
                                    pairsTouched: true });
    await visit(win2);
    ok(x180(win2, 'q1') === 0.25 && x180(win2, 'q3') === 0.25,
       'A4: a draft that never prefilled prefills every row');
  }

  // ---- A5: Start over ------------------------------------------------------------
  {
    const win = makeWorld();
    setCount(win, 2);
    await visit(win);
    ok(x180(win, 'q1') === 0.25, 'setup: first chip prefilled');
    win.document.getElementById('gen-reset').click();
    win.QuamGen.state.spec.instruments.controllers =
      [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }];
    setCount(win, 2);
    ok(x180(win, 'q1') === undefined, 'setup: the fresh chip starts blank');
    await visit(win);
    ok(x180(win, 'q1') === 0.25 && x180(win, 'q2') === 0.25,
       'A5: after Start over the fresh chip prefills again');
  }

  // ---- A6: an explicit Apply is not row-filtered -----------------------------------
  {
    const win = makeWorld();
    setCount(win, 3);
    await visit(win);
    delete pop(win).pulses.q2.x180_amplitude;
    const rep = win.QuamGen._test.applyPreset(JSON.parse(JSON.stringify(PRESET)), false);
    ok(rep.applied >= 1 && x180(win, 'q2') === 0.25,
       'A6: an explicit fill-empty Apply still reaches every row');
  }

  // ---- A7: a qubit deleted on the step-4 board, then re-created by the count ------
  {
    const win = makeWorld({ board: true });
    win.QuamGen.goToStep(4);
    setCount(win, 5);
    await visit(win);
    ok(x180(win, 'q5') === 0.25, 'setup: q5 prefilled');
    win.WiringGrid._removeQubit('q5');
    ok(win.QuamGen.state.spec.qubits.indexOf('q5') < 0 && x180(win, 'q5') === undefined,
       'setup: the board deleted q5 and its populate');
    setCount(win, 5);
    await visit(win);
    ok(x180(win, 'q5') === 0.25,
       'A7: a q5 re-created after a board delete prefills again');
  }

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('generate_autopreset_rows_selfcheck: all checks passed');
})().catch(function (e) { console.error((e && e.stack) || e); process.exit(1); });
