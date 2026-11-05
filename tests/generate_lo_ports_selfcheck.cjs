/* jsdom behavioral check: the step-6 MW-FEM LO model is ONE LO PER PORT, and
 * coupled ports share only a BAND (QA F16, regenerate-r2-05, regenerate-r2-07).
 *
 * QM docs (Guides/opx1000_fems.md): "Each analog output port must define
 * either an `upconverter_frequency` field with a frequency in the port's band,
 * or a `upconverters` field" and "Coupled ports must be in the same band, or
 * in bands `1` and `3`." The wizard used to solve one LO per coupled pair
 * (Out2+Out3, ...), so the customer chip's Out2 = 4.7 GHz / Out3 = 3.5 GHz
 * read as "one LO cannot cover them", and "Re-solve LOs" wrote the pair's
 * midpoint into the built chip (|IF| 654-747 MHz).
 *
 * Pins:
 *  L1. two drives on coupled Out2/Out3 1.49 GHz apart: no LO warning, each
 *      port's LO within ±0.4 GHz of its own RF (F16a).
 *  L2. coupled ports in bands 2 + 1 warn (naming both ports); bands 1 + 3 do
 *      not (F16b).
 *  L3. regenerate: the LO map shows the LO + band the BUILD uses (the chip's
 *      6.68 GHz band 3), not the solver's pick (F16c).
 *  L4. regenerate "Re-solve LOs" never writes an infeasible pick: a port no
 *      single LO covers keeps its stored LO and is not marked touched;
 *      applyLoAssignments honours opts.unsolved (r2-05).
 *  L5. an explicit band that does not cover the row's LO/RF flags the band
 *      cell and the conflict panel; the coupled-band rule names the pair;
 *      Review counts the findings; band back to 1 clears them; the stored LO
 *      is never rewritten by a band edit (r2-07).
 *  L6. the LF-FEM delay summary uses the explicit band run_build writes.
 *  L7. a step-6 deep link with no wiring allocation asks for one, and the
 *      answer renders the LO map (F16 deep-link sub-claim).
 *
 * Run:  node tests/generate_lo_ports_selfcheck.cjs   (driven by test_generate_lo_ports.py)
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

function makeWorld(fetchImpl) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win._fetches = [];
  win.fetch = fetchImpl ? fetchImpl(win) : function (url) {
    win._fetches.push(String(url));
    return new win.Promise(function () {});
  };
  new win.Function(GEN_JS).call(win);
  return win;
}

function spec(populate, qubits) {
  return {
    network: { host: '1.2.3.4', cluster_name: 'C', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 3, fem: 'mw' }] }],
                   opx_plus: [], octaves: [] },
    qubits: qubits || ['q1', 'q2', 'q3', 'q4'], qubit_pairs: [], twpas: [],
    lines: [], pair_gate: 'cz_tunable', populate: populate
  };
}
function xy(port) { return [{ con: 1, slot: 3, port: port, io_type: 'output' }]; }
const RR = [{ con: 1, slot: 3, port: 1, io_type: 'output' },
            { con: 1, slot: 3, port: 1, io_type: 'input' }];

function world(mode, populate, qubits) {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec(spec(populate, qubits), { mode: mode });
  return { win: win, G: G, T: G._test, st: G._test.state };
}
function panelText(win) {
  return win.document.getElementById('gen-band-warnings').textContent;
}

// ---- L1: coupled drives 1.49 GHz apart are two LOs, not one "span" -------
(function () {
  const w = world('generate', { qubit: {
    q1: { RF_freq: 4.89543e9 }, q2: { RF_freq: 3.40081e9 } } }, ['q1', 'q2']);
  w.st.allocation = { q1: { xy: xy(2) }, q2: { xy: xy(3) } };
  const calc = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(calc.warnings.length === 0,
    'L1: no LO/band warning for Out2 4.9 GHz + Out3 3.4 GHz (got ' +
    JSON.stringify(calc.warnings.map(function (x) { return x.message; })) + ')');
  ok(Math.abs(calc.assignments['qubit/q1'] - 4.89543e9) <= 0.4e9 &&
     Math.abs(calc.assignments['qubit/q2'] - 3.40081e9) <= 0.4e9,
    'L1: each port LO is within ±0.4 GHz of its own RF (got ' +
    calc.assignments['qubit/q1'] + ', ' + calc.assignments['qubit/q2'] + ')');
  ok(calc.groups.length === 2 && calc.groups[0].id !== calc.groups[1].id,
    'L1: one LO group per port');
  ok(Object.keys(calc.unsolved).length === 0, 'L1: both ports solvable');
})();

// ---- L2: the coupled-band rule ------------------------------------------
(function () {
  const w = world('generate', { qubit: {
    q1: { RF_freq: 6.0e9 }, q2: { RF_freq: 3.4e9 } } }, ['q1', 'q2']);
  w.st.allocation = { q1: { xy: xy(2) }, q2: { xy: xy(3) } };
  let msgs = w.T.loBandFindings(w.T.computeLoAssignments()).warnings
    .map(function (x) { return x.message; });
  const coupled = msgs.filter(function (m) { return /coupled/.test(m); });
  ok(coupled.length === 1 && /Out2 \(band 2/.test(coupled[0]) &&
     /Out3 \(band 1/.test(coupled[0]),
    'L2: bands 2 + 1 on coupled Out2/Out3 warn, naming both (got ' +
    JSON.stringify(msgs) + ')');
  w.st.spec.populate.qubit.q1.RF_freq = 7.8e9;          // band 3
  msgs = w.T.loBandFindings(w.T.computeLoAssignments()).warnings
    .map(function (x) { return x.message; });
  ok(msgs.filter(function (m) { return /coupled/.test(m); }).length === 0,
    'L2: bands 3 + 1 are legal (got ' + JSON.stringify(msgs) + ')');
})();

// ---- L3: the LO map shows what the build uses ----------------------------
(function () {
  const w = world('regenerate', {
    resonator: {
      q1: { RF_freq: 6.47725e9, LO_frequency: 6.68e9, band: 3 },
      q2: { RF_freq: 6.4772e9, LO_frequency: 6.68e9, band: 3 } } }, ['q1', 'q2']);
  w.st.allocation = { q1: { rr: RR }, q2: { rr: RR } };
  w.T.recomputeLOs();
  const map = w.win.document.getElementById('gen-lo-map').textContent;
  ok(/6\.68 GHz · band 3/.test(map),
    'L3: the LO map shows the chip LO 6.68 GHz in band 3 (got ' + map + ')');
  ok(!/band 2/.test(map), 'L3: no solver band-2 LO in the map');
  // the customer chip's q1 readout sits at 6.477 GHz under a band-3 LO of
  // 6.68 GHz: the PORT frequency is in band, which is what Diagnostics
  // judges — no band finding on a chip that runs (the RF is not the port's).
  const t3 = panelText(w.win);
  ok(!/does not cover/.test(t3) && !/coupled/.test(t3),
    'L3: an in-band LO with an RF just below the band edge raises nothing (got ' + t3 + ')');
  ok(w.st.spec.populate.resonator.q1.LO_frequency === 6.68e9,
    'L3: the chip LO is kept (fill-only-empty)');
})();

// ---- L8: a resonator added to a shared feedline keeps the source LO -------
(function () {
  const w = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.10e9, LO_frequency: 7.46e9 },
      q2: { RF_freq: 7.15e9, LO_frequency: 7.46e9 },
      q3: { RF_freq: 7.80e9 } } }, ['q1', 'q2', 'q3']);
  // q3 is the new row: the source chip never had it
  delete w.st.regenBaselinePopulate.resonator.q3;
  w.st.allocation = { q1: { rr: RR }, q2: { rr: RR }, q3: { rr: RR } };
  const calc = w.T.computeLoAssignments();
  ok(calc.assignments['resonator/q3'] === 7.46e9,
    'L8: the shared feedline keeps the source LO 7.46 GHz (got ' +
    calc.assignments['resonator/q3'] + ')');
  ok(calc.groups.length === 1 && calc.groups[0].loFreq === 7.46e9,
    'L8: the LO map shows the source LO (got ' + JSON.stringify(calc.groups) + ')');
  // an explicit Re-solve still re-derives
  const forced = w.T.computeLoAssignments({ force: true });
  ok(forced.assignments['resonator/q3'] !== 7.46e9,
    'L8: Re-solve LOs re-derives (got ' + forced.assignments['resonator/q3'] + ')');
  // an LO that no longer covers every member is not kept
  const w2 = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.10e9, LO_frequency: 7.46e9 },
      q3: { RF_freq: 7.465e9 } } }, ['q1', 'q3']);
  delete w2.st.regenBaselinePopulate.resonator.q3;
  w2.st.allocation = { q1: { rr: RR }, q3: { rr: RR } };
  const c2 = w2.T.computeLoAssignments();
  ok(c2.assignments['resonator/q3'] !== 7.46e9,
    'L8: a source LO inside a new resonator demod floor is re-derived (got ' +
    c2.assignments['resonator/q3'] + ')');
  // a new member beyond the ±0.4 GHz window is not covered: re-derived
  const w3 = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.10e9, LO_frequency: 7.46e9 },
      q3: { RF_freq: 7.95e9 } } }, ['q1', 'q3']);
  delete w3.st.regenBaselinePopulate.resonator.q3;
  w3.st.allocation = { q1: { rr: RR }, q3: { rr: RR } };
  ok(w3.T.computeLoAssignments().assignments['resonator/q3'] !== 7.46e9,
    'L8: a new member beyond the window re-derives');
  // the source split this port (two LOs among its members): re-derived
  const w4 = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.10e9, LO_frequency: 7.46e9 },
      q2: { RF_freq: 7.15e9, LO_frequency: 7.30e9 },
      q3: { RF_freq: 7.2e9 } } }, ['q1', 'q2', 'q3']);
  delete w4.st.regenBaselinePopulate.resonator.q3;
  w4.st.allocation = { q1: { rr: RR }, q2: { rr: RR }, q3: { rr: RR } };
  const a4 = w4.T.computeLoAssignments().assignments['resonator/q3'];
  ok(a4 !== 7.46e9 && a4 !== 7.30e9,
    'L8: two source LOs on one port re-derive (got ' + a4 + ')');
})();

// ---- L9: a member the source chip already ran is not judged; a rename follows
(function () {
  // q1 sits 4 MHz from the source LO (the lab's own chip): keeping the LO keeps the
  // chip as it was, and the new q3 fits, so the LO stays
  const w = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.464e9, LO_frequency: 7.46e9 },
      q2: { RF_freq: 7.15e9, LO_frequency: 7.46e9 },
      q3: { RF_freq: 7.2e9 } } }, ['q1', 'q2', 'q3']);
  delete w.st.regenBaselinePopulate.resonator.q3;
  w.st.allocation = { q1: { rr: RR }, q2: { rr: RR }, q3: { rr: RR } };
  const c = w.T.computeLoAssignments();
  ok(c.assignments['resonator/q3'] === 7.46e9,
    'L9: an unchanged source member inside the demod floor does not move the LO (got ' +
    c.assignments['resonator/q3'] + ')');
  // ...nor one the source ran outside the ±0.4 GHz window
  const w2 = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.90e9, LO_frequency: 7.46e9 },
      q2: { RF_freq: 7.15e9, LO_frequency: 7.46e9 },
      q3: { RF_freq: 7.2e9 } } }, ['q1', 'q2', 'q3']);
  delete w2.st.regenBaselinePopulate.resonator.q3;
  w2.st.allocation = { q1: { rr: RR }, q2: { rr: RR }, q3: { rr: RR } };
  ok(w2.T.computeLoAssignments().assignments['resonator/q3'] === 7.46e9,
    'L9: an unchanged source member outside the window does not move the LO');
  // ...but the same member with its RF EDITED beyond the window re-derives
  w2.st.spec.populate.resonator.q1.RF_freq = 7.95e9;
  ok(w2.T.computeLoAssignments().assignments['resonator/q3'] !== 7.46e9,
    'L9: a source member whose RF the user moved beyond the window re-derives');
  // a qubit renamed on the wizard keeps its source row through the id record
  const w3 = world('regenerate', {
    resonator: {
      q1: { RF_freq: 7.10e9, LO_frequency: 7.46e9 },
      q2: { RF_freq: 7.15e9, LO_frequency: 7.46e9 },
      q3: { RF_freq: 7.4e9 } } }, ['q1', 'q2', 'q3']);
  delete w3.st.regenBaselinePopulate.resonator.q3;
  w3.st.spec.populate.resonator.qA1 = w3.st.spec.populate.resonator.q1;
  delete w3.st.spec.populate.resonator.q1;
  w3.st.spec.populate.resonator.qA2 = w3.st.spec.populate.resonator.q2;
  delete w3.st.spec.populate.resonator.q2;
  w3.st.spec.qubits = ['qA1', 'qA2', 'q3'];
  w3.st.regenQubitSource = { qA1: 'q1', qA2: 'q2', q3: 'q3' };
  w3.st.allocation = { qA1: { rr: RR }, qA2: { rr: RR }, q3: { rr: RR } };
  const c3 = w3.T.computeLoAssignments();
  ok(c3.assignments['resonator/q3'] === 7.46e9,
    'L9: renamed source members still keep the feedline LO (got ' +
    c3.assignments['resonator/q3'] + ')');
})();

// ---- L4: Re-solve never writes an infeasible pick ------------------------
(function () {
  // q1 + q3 share Out2 and sit 1.49 GHz apart: no single upconverter covers
  // them. q2 is alone on Out3 (feasible).
  const w = world('regenerate', { qubit: {
    q1: { RF_freq: 4.89543e9, LO_frequency: 4.7e9, band: 1 },
    q3: { RF_freq: 3.40081e9, LO_frequency: 4.7e9, band: 1 },
    q2: { RF_freq: 3.40081e9, LO_frequency: 3.5e9, band: 1 } } }, ['q1', 'q2', 'q3']);
  w.st.allocation = { q1: { xy: xy(2) }, q3: { xy: xy(2) }, q2: { xy: xy(3) } };
  const calc = w.T.computeLoAssignments();
  ok(calc.unsolved['qubit/q1'] === 'span' && calc.unsolved['qubit/q3'] === 'span',
    'L4: the over-wide port is reported unsolved (got ' + JSON.stringify(calc.unsolved) + ')');
  w.T.recomputeLOs({ force: true });
  const q = w.st.spec.populate.qubit;
  ok(q.q1.LO_frequency === 4.7e9 && q.q3.LO_frequency === 4.7e9,
    'L4: a forced re-solve keeps an infeasible port\'s stored LO (got ' +
    q.q1.LO_frequency + ', ' + q.q3.LO_frequency + ')');
  ok(!(w.st.regenTouched || {})['qubit|q1|LO_frequency'],
    'L4: the kept LO is not marked touched');
  ok(Math.abs(q.q2.LO_frequency - 3.40081e9) <= 0.4e9 &&
     q.q2.LO_frequency !== 3.5e9 && w.st.regenTouched['qubit|q2|LO_frequency'] === 1,
    'L4: a feasible port IS re-solved and marked touched (got ' + q.q2.LO_frequency + ')');
  ok(/span/.test(panelText(w.win)) || /cannot cover/.test(panelText(w.win)),
    'L4: the over-wide port still warns');
  // one port, one upconverter: two stored LOs on Out2 is a named finding
  q.q3.LO_frequency = 3.5e9;
  const two = w.T.loBandFindings(w.T.computeLoAssignments()).warnings
    .filter(function (x) { return /different LOs/.test(x.message); });
  ok(two.length === 1 && /Out2/.test(two[0].message),
    'L4: two different LOs on one port warn (got ' + JSON.stringify(two) + ')');
  q.q3.LO_frequency = 4.7e9;
  // the seam itself
  w.T.applyLoAssignments({ 'qubit/q2': 4.148e9 },
    { force: true, unsolved: { 'qubit/q2': 'span' } });
  ok(q.q2.LO_frequency !== 4.148e9, 'L4: opts.unsolved guards the forced write');
})();

// ---- L5: band override checks (the r2-07 journey) ------------------------
(function () {
  const w = world('regenerate', { qubit: {
    q3: { RF_freq: 4.763e9, LO_frequency: 4.6e9, band: 1 },
    q4: { RF_freq: 3.455e9, LO_frequency: 3.5e9, band: 1 } } }, ['q3', 'q4']);
  w.st.allocation = { q3: { xy: xy(4) }, q4: { xy: xy(5) } };
  w.T.renderPopulateTables();
  w.T.recomputeLOs();
  ok(!/band/.test(panelText(w.win)), 'L5: the chip as built has no band finding (got ' +
    panelText(w.win) + ')');
  const sel = w.win.document.querySelector(
    '.gen-pop-in[data-group="qubit"][data-rid="q4"][data-field="band"]');
  ok(!!sel, 'L5: q4 band select renders');
  sel.value = '2';
  sel.dispatchEvent(new w.win.Event('change', { bubbles: true }));
  const txt = panelText(w.win);
  ok(sel.classList.contains('gen-cell-warn') && /Band 2 covers/.test(sel.title),
    'L5: band 2 on a 3.455 GHz row flags the cell (cls=' + sel.className +
    ' title=' + sel.title + ')');
  ok(/q4: band 2/.test(txt) && /does not cover/.test(txt),
    'L5: the panel says band 2 does not cover q4 (got ' + txt + ')');
  ok(/Out4 \(band 1: q3\) and Out5 \(band 2: q4\) are coupled/.test(txt),
    'L5: the panel names the coupled Out4/Out5 band clash (got ' + txt + ')');
  ok(w.st.spec.populate.qubit.q4.LO_frequency === 3.5e9,
    'L5: a band edit never rewrites the stored LO');
  w.G.goToStep(8);
  const review = w.win.document.getElementById('gen-review').textContent;
  ok(/LO \/ band \/ power conflicts/.test(review), 'L5: Review counts the findings (got ' +
    review.slice(0, 300) + ')');
  w.G.goToStep(6);
  const sel2 = w.win.document.querySelector(
    '.gen-pop-in[data-group="qubit"][data-rid="q4"][data-field="band"]');
  sel2.value = '1';
  sel2.dispatchEvent(new w.win.Event('change', { bubbles: true }));
  ok(!sel2.classList.contains('gen-cell-warn') && !/band/.test(panelText(w.win)),
    'L5: band back to 1 clears the flag and the panel (got ' + panelText(w.win) + ')');
  // bands 3 + 1 on coupled ports are legal
  w.st.spec.populate.qubit.q3 = { RF_freq: 7.1e9, LO_frequency: 7.0e9, band: 3 };
  w.T.recomputeLOs({ noApply: true });
  ok(!/coupled/.test(panelText(w.win)),
    'L5: bands 3 + 1 on Out4/Out5 raise no coupled warning (got ' + panelText(w.win) + ')');
})();

// ---- L6: the flux-delay summary reads the explicit band ------------------
(function () {
  const w = world('regenerate', { qubit: {
    q1: { RF_freq: 7.1e9, LO_frequency: 7.0e9, band: 3 } } }, ['q1']);
  w.st.allocation = { q1: { xy: xy(2) } };
  w.T.renderPopulateTables();
  w.T.recomputeLOs();
  const host = w.win.document.getElementById('gen-pop-flux-delay-summary');
  const t = host ? host.textContent : '';
  ok(/141 ns/.test(t) && /band 3/.test(t),
    'L6: LO 7.0 GHz with explicit band 3 -> 141 ns (band 3), not band 2 (got ' + t + ')');
})();

// ---- L8: coupled ports with AUTOMATIC bands share the band that covers both
// docs/301 F33: a 4.9 GHz drive on Out1 (derived band 1) beside a 7.1 GHz
// readout read on In1 (derived band 2) -- the crossing default -- used to be
// a conflict every new chip had to fix by hand; band 2 covers both LOs.
(function () {
  const RR8 = [{ con: 1, slot: 3, port: 8, io_type: 'output' },
               { con: 1, slot: 3, port: 1, io_type: 'input' }];
  const w = world('generate', {
    qubit: { q1: { RF_freq: 4.9e9 } },
    resonator: { q1: { RF_freq: 7.1e9 } } }, ['q1']);
  w.st.allocation = { q1: { xy: xy(1), rr: RR8 } };
  let calc = w.T.loBandFindings(w.T.computeLoAssignments());
  const coupled = calc.warnings.filter(function (x) { return /coupled/.test(x.message); });
  ok(coupled.length === 0,
    'L8: auto bands on coupled Out1/In1 raise no conflict (got ' +
    JSON.stringify(calc.warnings.map(function (x) { return x.message; })) + ')');
  ok(calc.coBands['qubit/q1'] === 2 && calc.coBands['resonator/q1'] === 2,
    'L8: both rows get band 2 (got ' + JSON.stringify(calc.coBands) + ')');
  ok(calc.notes.length === 1 && /share a band: the build uses band 2/.test(calc.notes[0].message),
    'L8: the shared band is named (got ' + JSON.stringify(calc.notes) + ')');
  const sp = w.T.specWithCoBands();
  ok(sp.populate.qubit.q1.band === 2 && sp.populate.resonator.q1.band === 2,
    'L8: the build spec carries band 2 on both rows (got ' +
    JSON.stringify(sp.populate) + ')');
  ok(w.st.spec.populate.qubit.q1.band == null,
    'L8: the step-6 row itself stays automatic');
  // a band a person set is never moved: it warns, naming the fix
  w.st.spec.populate.qubit.q1.band = 1;
  calc = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(calc.warnings.some(function (x) { return /coupled/.test(x.message) && /Band 2/.test(x.message); }),
    'L8: an explicit band 1 still warns and names band 2 as the fix (got ' +
    JSON.stringify(calc.warnings.map(function (x) { return x.message; })) + ')');
  ok(Object.keys(calc.coBands).length === 0 &&
     w.T.specWithCoBands().populate.qubit.q1.band === 1,
    'L8: and the build keeps the band the person set');
  ok(/spec: specWithCoBands\(\), output_path/.test(GEN_JS),
    'L8: the build request carries specWithCoBands(), not the raw spec');
})();


// ---- L8d: a coupled pair no single band covers takes bands 1 + 3 ---------
// QM docs (Guides/opx1000_fems.md#bands): "Coupled ports must be in the same
// band, or in bands `1` and `3`." A 4.0 GHz drive on Out1 (band 1 only) and a
// 7.0 GHz readout read on In1: band 3 on the readout row is the legal answer.
(function () {
  const RR8 = [{ con: 1, slot: 3, port: 8, io_type: 'output' },
               { con: 1, slot: 3, port: 1, io_type: 'input' }];
  const w = world('generate', {
    qubit: { q1: { RF_freq: 4.0e9 } },
    resonator: { q1: { RF_freq: 7.0e9 } } }, ['q1']);
  w.st.allocation = { q1: { xy: xy(1), rr: RR8 } };
  const calc = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(!calc.warnings.some(function (x) { return x.kind === 'coupled_band'; }),
    'L8d: auto bands find the 1 + 3 combination (got ' +
    JSON.stringify(calc.warnings.map(function (x) { return x.message; })) + ')');
  ok(calc.coBands['resonator/q1'] === 3 && (calc.coBands['qubit/q1'] == null || calc.coBands['qubit/q1'] === 1),
    'L8d: the readout row gets band 3, the drive stays band 1 (got ' + JSON.stringify(calc.coBands) + ')');
  ok(w.T.coupledBandBlockers().length === 0, 'L8d: nothing blocks the build');
  // the walk's typed bands: drive band 1, readout band 2 on coupled Out1/In1
  w.st.spec.populate.qubit.q1 = { RF_freq: 5.26e9, LO_frequency: 5.26e9, band: 1 };
  w.st.spec.populate.resonator.q1 = { RF_freq: 7.0e9, LO_frequency: 7.0e9, band: 2 };
  const bl = w.T.coupledBandBlockers();
  ok(bl.length === 1 && /Band 2/.test(bl[0].message),
    'L8d: typed bands 1 + 2 on coupled ports block the build, naming the fix (got ' +
    JSON.stringify(bl.map(function (x) { return x.message; })) + ')');
  ok(/var bandErrs = coupledBandBlockers\(\);\s*if \(bandErrs\.length\)[\s\S]{0,1400}revealResult\(bEl\);\s*return;/.test(GEN_JS),
    'L8d: the build path refuses while a coupled-band blocker stands');
})();

// ---- L8e: on QOP <= 3.4 bands 1 + 3 are not a legal pair -----------------
// hidden_pages/QOP33x.md:111 and QOP34x.md:59: "**Bands 1 & 3 cannot be used
// in coupled ports**." The L8d world with the spec declaring its QOP; and the
// port-sharing known issue (QOP33x.md:112, QOP34x.md:91, QOP36x.md:78; fixed
// QOP35x.md:75, QOP37x.md:97) is a non-blocking note.
(function () {
  const RR8 = [{ con: 1, slot: 3, port: 8, io_type: 'output' },
               { con: 1, slot: 3, port: 1, io_type: 'input' }];
  const w = world('generate', {
    qubit: { q1: { RF_freq: 4.0e9 } },
    resonator: { q1: { RF_freq: 7.0e9 } } }, ['q1']);
  w.st.allocation = { q1: { xy: xy(1), rr: RR8 } };
  w.st.spec.qop_version = '3.4.2';
  const calc = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(calc.warnings.some(function (x) { return x.kind === 'coupled_band'; }),
    'L8e: on QOP 3.4 the 1 + 3 answer is not offered, the pair stays a warning (got ' +
    JSON.stringify(calc.warnings.map(function (x) { return x.message; })) + ')');
  ok(calc.coBands['resonator/q1'] !== 3,
    'L8e: band 3 is never proposed on QOP 3.4 (got ' + JSON.stringify(calc.coBands) + ')');
  ok(w.T.coupledBandBlockers().length === 1, 'L8e: the build is blocked on QOP 3.4');
  ok(calc.qopNotes.some(function (n) { return n.kind === 'port_sharing' && /stop playing/.test(n.message); }),
    'L8e: the port-sharing known issue is noted on QOP 3.4');
  w.st.spec.qop_version = 'v3_8_1';
  const calc2 = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(calc2.coBands['resonator/q1'] === 3 && w.T.coupledBandBlockers().length === 0,
    'L8e: on QOP 3.8.1 the 1 + 3 answer is back (got ' + JSON.stringify(calc2.coBands) + ')');
  ok(!calc2.qopNotes.some(function (n) { return n.kind === 'port_sharing'; }),
    'L8e: no port-sharing note on QOP 3.8.1');
  delete w.st.spec.qop_version;
  const calc3 = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(calc3.coBands['resonator/q1'] === 3, 'L8e: an undeclared QOP keeps the current rule');
  ok(calc3.qopNotes.some(function (n) { return n.kind === 'port_sharing' && /QOP version not set/.test(n.message); }),
    'L8e: an undeclared QOP keeps the note and says the version is undeclared');
  // Q1: the QOP caveats are short lines, version-aware, and only on Review
  let lines = w.T.qopReviewLines(calc3);
  ok(lines.some(function (t) { return /Bands 1 \+ 3 on a coupled pair need QOP 3\.5/.test(t); }) &&
     lines.some(function (t) { return /Set the QOP version/.test(t); }),
    'Q1: unset QOP names the 1 + 3 caveat and asks for the version (got ' + JSON.stringify(lines) + ')');
  ok(lines.every(function (t) { return t.length <= 170 && !/ -- /.test(t); }),
    'Q1: every caveat is one short line (got ' + JSON.stringify(lines) + ')');
  w.G.goToStep(8);
  let doc = w.win.document;
  const qsel = doc.getElementById('gen-qop-version');
  ok(!!qsel && qsel.value === '' && doc.querySelector('#gen-review #gen-qop-version') === qsel,
    'Q1: the Review step carries the QOP version select, unset by default');
  ok(!/QOP note/.test(doc.querySelector('.gen-review-table').textContent),
    'Q1: the review table no longer carries a QOP note wall');
  qsel.value = '3.4';
  w.st._buildForce = true; w.st._buildAck = true;
  w.st._buildAckSource = true; w.st._buildAckScripts = true;
  qsel.dispatchEvent(new w.win.Event('change', { bubbles: true }));
  ok(w.st._buildForce && w.st._buildAck && w.st._buildAckSource && w.st._buildAckScripts,
    'Q2: selecting QOP preserves build acknowledgements');
  ok(w.st.spec.qop_version === '3.4', 'Q1: picking 3.4 writes spec.qop_version');
  const cav = doc.getElementById('gen-qop-caveats').textContent;
  ok(/QOP 3\.4: coupled MW ports must share one band/.test(cav) && /QOP 3\.4 known issue/.test(cav),
    'Q1: on 3.4 the same-band rule and the known issue are listed (got ' + cav + ')');
  ok(w.T.coupledBandBlockers().length === 1, 'Q1: the 3.4 pick reaches the band gate');
  ok(!/bands 1 and 3\./.test(w.T.coupledBandBlockers()[0].message),
    'Q1: on 3.4 the blocker does not offer bands 1 and 3 (got ' + w.T.coupledBandBlockers()[0].message + ')');
  const q2 = doc.getElementById('gen-qop-version');
  q2.value = '';
  q2.dispatchEvent(new w.win.Event('change', { bubbles: true }));
  ok(!('qop_version' in w.st.spec), 'Q1: back to Not set removes the key (unset = as before)');
  w.st.spec.instruments.controllers[0].fems[0].fem = 'lf';
  w.G.goToStep(8);
  ok(!doc.getElementById('gen-qop-version') &&
     !/QOP version/.test(doc.querySelector('.gen-review-table').textContent),
    'Q3: a chip without MW-FEM has no QOP row');
})();

// ---- L8a: a row rewrite also reaches its other coupled port ---------------
(function () {
  function port(n, io) { return { con: 1, slot: 3, port: n, io_type: io }; }
  function scenario(rf, reverse) {
    const w = world('generate', {
      qubit: { q1: { RF_freq: 6.0e9 }, q2: { RF_freq: 4.0e9 } },
      resonator: { q1: { RF_freq: 5.2e9 }, q2: { RF_freq: rf } } }, ['q1', 'q2']);
    w.st.allocation = {
      q1: { xy: xy(reverse ? 8 : 1),
            rr: [port(reverse ? 1 : 8, 'output'), port(reverse ? 2 : 1, 'input')] },
      q2: { xy: xy(4), rr: [port(2, 'output'), port(reverse ? 1 : 2, 'input')] }
    };
    return w;
  }
  [false, true].forEach(function (reverse) {
    const w = scenario(5.25e9, reverse);
    const calc = w.T.loBandFindings(w.T.computeLoAssignments());
    ok(!calc.warnings.some(function (x) { return /coupled/.test(x.message); }),
      'L8a: a satisfiable chain has no coupled warning (reverse=' + reverse + ')');
    ok(calc.coBands['qubit/q1'] === 2 && calc.coBands['resonator/q1'] === 2 &&
       calc.coBands['resonator/q2'] === 2,
      'L8a: band 2 propagates through both resonator ports (reverse=' + reverse + ')');
    const sp = w.T.specWithCoBands();
    ok(sp.populate.resonator.q2.band === 2,
      'L8a: the third port row carries band 2 into the build');
    w.st.spec = sp;
    const built = w.T.loBandFindings(w.T.computeLoAssignments());
    ok(!built.warnings.some(function (x) { return /coupled/.test(x.message); }),
      'L8a: every coupled pair remains compatible after applying the build bands');
  });
  // A frequency outside band 2 prevents any assignment for the full chain.
  const w = scenario(3.0e9, false);
  const calc = w.T.loBandFindings(w.T.computeLoAssignments());
  ok(Object.keys(calc.coBands).length === 0,
    'L8b: an unsatisfiable chain discards every tentative row rewrite');
  ok(calc.warnings.some(function (x) { return /Out1/.test(x.message) && /In1/.test(x.message) && /coupled/.test(x.message); }),
    'L8b: the original coupled conflict stays visible');
  ok(calc.notes.length === 0,
    'L8b: Review never claims a shared build band for a discarded assignment');
  ok(w.T.specWithCoBands().populate.resonator.q1.band == null,
    'L8b: the rejected assignment leaves the build rows automatic');
  const locked = scenario(5.25e9, false);
  locked.st.spec.populate.resonator.q2.band = 1;
  const explicit = locked.T.loBandFindings(locked.T.computeLoAssignments());
  ok(Object.keys(explicit.coBands).length === 0 && explicit.notes.length === 0 &&
     explicit.warnings.some(function (x) { return /coupled/.test(x.message); }) &&
     locked.T.specWithCoBands().populate.resonator.q2.band === 1,
    'L8c: an explicit band on the third port is preserved and blocks an incompatible rewrite');
})();


// ---- L9 (G2): a typed LO is never re-derived by a later RF edit ----------
(function () {
  const RR = [{ con: 1, slot: 3, port: 8, io_type: 'output' },
              { con: 1, slot: 3, port: 2, io_type: 'input' }];
  const w = world('generate', { resonator: {
    q1: { RF_freq: 6.95e9 }, q2: {} } }, ['q1', 'q2']);
  w.st.allocation = { q1: { rr: RR }, q2: { rr: RR } };
  w.T.renderPopulateTables();
  w.T.recomputeLOs();
  function cell(rid, f) {
    return w.win.document.querySelector(
      '.gen-pop-in[data-group="resonator"][data-rid="' + rid + '"][data-field="' + f + '"]');
  }
  function commit(el, v) {
    el.value = v;
    el.dispatchEvent(new w.win.Event('input', { bubbles: true }));
    el.dispatchEvent(new w.win.Event('change', { bubbles: true }));
  }
  const lo1 = cell('q1', 'LO_frequency');
  ok(!!lo1, 'L9: the q1 resonator LO cell renders');
  commit(lo1, '7');
  const typed = w.st.spec.populate.resonator.q1.LO_frequency;
  ok(Math.abs(typed - 7e9) < 1, 'L9: the typed 7 GHz LO is stored (got ' + typed + ')');
  commit(cell('q2', 'RF_freq'), '7.25');
  w.T.recomputeLOs();
  const a = w.st.spec.populate.resonator.q1.LO_frequency;
  const b = w.st.spec.populate.resonator.q2.LO_frequency;
  ok(Math.abs(a - 7e9) < 1, 'L9: the next RF edit leaves the typed LO alone (got ' + a + ')');
  ok(Math.abs(b - 7e9) < 1, 'L9: the other element on the port takes the typed LO (got ' + b + ')');
  const tag = lo1.parentNode.querySelector('.gen-lo-tag');
  ok(tag && /typed/.test(tag.textContent) && /no longer derived/.test(tag.title),
    'L9: the cell says the LO is typed and not derived (got ' + (tag && tag.textContent) + ')');
  commit(lo1, '');
  w.T.recomputeLOs();
  ok(w.st.spec.populate.resonator.q1.LO_frequency !== '' &&
     Math.abs(w.st.spec.populate.resonator.q1.LO_frequency - 7e9) > 1,
    'L9: an emptied cell is derived again (got ' + w.st.spec.populate.resonator.q1.LO_frequency + ')');
})();

// ---- L7: step-6 deep link with no allocation -----------------------------
(async function () {
  const alloc = { q1: { xy: xy(2) }, q2: { xy: xy(3) } };
  const win = makeWorld(function (w) {
    return function (url) {
      w._fetches.push(String(url));
      if (/\/generate\/allocate/.test(String(url))) {
        return w.Promise.resolve({ json: function () {
          return w.Promise.resolve({ ok: true, result: { allocation: alloc } });
        } });
      }
      return new w.Promise(function () {});
    };
  });
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec(spec({ qubit: {
    q1: { RF_freq: 4.89543e9, LO_frequency: 4.7e9, band: 1 },
    q2: { RF_freq: 3.40081e9, LO_frequency: 3.5e9, band: 1 } } }, ['q1', 'q2']),
    { mode: 'regenerate' });
  G._test.state.env = 'C:/fake/python.exe';
  G._test.state.allocation = null;
  G.goToStep(6);
  ok(win._fetches.some(function (u) { return /\/generate\/allocate/.test(u); }),
    'L7: entering step 6 with no allocation asks for one (fetches ' +
    JSON.stringify(win._fetches) + ')');
  for (let i = 0; i < 20; i++) await new Promise(function (r) { setTimeout(r, 5); });
  const map = win.document.getElementById('gen-lo-map');
  ok(!!G._test.state.allocation && map && !map.hidden && /Out2/.test(map.textContent),
    'L7: the allocation answer renders the LO map (hidden=' + (map && map.hidden) +
    ' text=' + (map && map.textContent) + ')');
  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('generate_lo_ports_selfcheck: all checks passed');
})().catch(function (e) { console.error('L7 threw', e && e.stack); process.exit(1); });
