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
