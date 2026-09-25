/* jsdom behavioral check (QA regenerate-r2-04 / r2-06): the Re-generate build
 * asks before a changed port FSP moves every calibrated pulse on that port.
 *
 * /regenerate/build answers an unanswered manual-mode FSP change with
 * {needs_confirm, confirm_kind: "fsp", fsp_compensation: <Live Edit plan>};
 * the wizard opens Live Edit's own offer (window._openFspPopup) and:
 *  R1. "comp" records the answer keyed by port + new FSP (with the popup's
 *      amplitudes from window._fspCompUpdates) and re-POSTs with it; the body
 *      always carries power_mode + fsp_ack in regenerate mode.
 *  R2. "cancel" never re-POSTs and says the build was cancelled.
 *  R3. the result panel names the rescaled amplitudes (never silent).
 *  R4. a plain Generate posts neither field.
 *
 * Run:  node tests/generate_regen_fsp_selfcheck.cjs   (driven by test_generate_regen_fsp.py)
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
// The REAL ValueDelta IIFE from app.js (docs/76: one Δ implementation) —
// sliced like bulk_arith_selfcheck, the rest of app.js is not needed.
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const VD_START = APP_JS.indexOf('window.ValueDelta = (function () {');
if (VD_START < 0) { console.error('FAIL: ValueDelta not found in app.js'); process.exit(1); }
const VALUE_DELTA_JS = APP_JS.slice(VD_START, APP_JS.indexOf('\n})();', VD_START) + 6);

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }
async function settle() { for (let i = 0; i < 15; i++) await tick(); }

const PLAN = {
  fsp_path: 'ports.mw_outputs.con1.3.3.full_scale_power_dbm',
  port: 'con1/3/3 (q2 xy)', fsp_old: 0, fsp_new: -6, factor: 1.9953,
  amps: [{ path: 'qubits.q2.xy.operations.EF_x180.amplitude', channel: 'qubits.q2.xy',
           op: 'EF_x180', old: 0.07, new: 0.1397, clips: false }],
  skipped: [], clip_count: 0, range_warn: null, rows: [['qubit', 'q2']]
};

function makeWorld(buildReplies) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win._posts = [];
  win.fetch = function (url, opts) {
    url = String(url);
    const reply = function (body) {
      return win.Promise.resolve({ json: function () { return win.Promise.resolve(body); } });
    };
    if (/select-env/.test(url)) return reply({ ok: true });
    if (/\/(re)?generate\/build/.test(url)) {
      win._posts.push({ url: url, body: JSON.parse(opts.body) });
      return reply(buildReplies.shift() || { ok: false, error: 'no more replies' });
    }
    return new win.Promise(function () {});
  };
  win._popups = [];
  win._openFspPopup = function (plan, cb) { win._popups.push({ plan: plan, cb: cb }); };
  win._fspCompUpdates = function (plan) {
    return plan.amps.map(function (a) { return { dot_path: a.path, value: '0.14' }; });
  };
  new win.Function(VALUE_DELTA_JS).call(win);
  new win.Function(GEN_JS).call(win);
  return win;
}

function hydrate(win, mode) {
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'C', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 3, fem: 'mw' }] }],
                   opx_plus: [], octaves: [] },
    qubits: ['q1', 'q2'], qubit_pairs: [], twpas: [], lines: [],
    pair_gate: 'cz_tunable',
    populate: { qubit: { q2: { full_scale_power_dbm: -6 } } }
  }, { mode: mode, buildEndpoint: mode === 'regenerate' ? '/regenerate/build' : undefined,
       sourcePath: 'D:\\src' });
  G._test.state.env = 'C:/py/python.exe';
  win.document.getElementById('gen-output-path').value = 'D:\\out\\chip';
  // gen-session (QA regenerate-r2-18) refuses a ticked scripts export with no
  // folder; give it the folder the output path implies (merged at integration).
  win.document.getElementById('gen-scripts-path').value = 'D:\\out\\chip\\state_gen_scripts';
  return G;
}

const DONE = { ok: true, status: 'ok', result: { qubits: ['q1', 'q2'], qubit_pairs: [] },
  merge: { carried: 10, grafted: 0, populate_protected: 1, populate_conflicts: [],
           fsp_compensated_total: 1,
           fsp_compensated: [{ path: PLAN.amps[0].path, old: 0.07, new: 0.14 }] } };
const ASK = { ok: false, needs_confirm: true, confirm_kind: 'fsp', fsp_pending: 1,
  fsp_compensation: PLAN, error: "A port's full-scale power changed." };

(async function () {
  // ---- R1 + R3: comp answer re-POSTs with the answer; result names it ----
  let win = makeWorld([JSON.parse(JSON.stringify(ASK)), JSON.parse(JSON.stringify(DONE))]);
  let G = hydrate(win, 'regenerate');
  G._test.runBuild();
  await settle();
  ok(win._posts.length === 1, 'R1: first build POST sent (got ' + win._posts.length + ')');
  const b1 = win._posts[0] && win._posts[0].body;
  ok(b1 && b1.power_mode === G._test.state.powerMode && b1.power_mode != null &&
     b1.fsp_ack && Object.keys(b1.fsp_ack).length === 0,
    'R1: a regen build carries power_mode + an (empty) fsp_ack (got ' +
    JSON.stringify(b1 && { pm: b1.power_mode, ack: b1.fsp_ack }) + ')');
  ok(win._popups.length === 1 && win._popups[0].plan.fsp_path === PLAN.fsp_path,
    'R1: the server offer opens the FSP popup');
  win._popups[0].cb('comp', win._popups[0].plan);
  await settle();
  ok(win._posts.length === 2, 'R1: the answer re-POSTs the build');
  const ack = win._posts[1] && win._posts[1].body.fsp_ack[PLAN.fsp_path];
  ok(ack && ack.mode === 'comp' && ack.fsp_new === -6 && ack.amps.length === 1 &&
     ack.amps[0].value === '0.14',
    'R1: the answer is keyed by port, names the new FSP and the popup amps (got ' +
    JSON.stringify(ack) + ')');
  const res = win.document.getElementById('gen-build-result').textContent;
  ok(/1 amplitude rescaled to keep power/.test(res),
    'R3: the result names the rescaled amplitude (got ' + res.slice(0, 300) + ')');
  const chip = win.document.querySelector('.gen-merge-fsp');
  ok(chip && chip.title.indexOf('EF_x180') >= 0, 'R3: its title lists the path');
  // Review of r2-04 / r2-06: old → new carries its Δ through ValueDelta
  // (docs/76), the way Live Edit's popup shows the same rescale.
  const vd = win.ValueDelta.compute(0.07, 0.14);
  ok(vd && vd.text === '+0.07' && vd.pct_text === '+100%',
    'R3: the harness runs the real ValueDelta (got ' + JSON.stringify(vd) + ')');
  ok(chip && chip.title.indexOf('0.07 → 0.14  (Δ +0.07, +100%)') >= 0,
    'R3: each rescaled amplitude shows its Δ (got ' + JSON.stringify(chip && chip.title) + ')');

  // ---- R2: cancel never builds ------------------------------------------
  win = makeWorld([JSON.parse(JSON.stringify(ASK)), JSON.parse(JSON.stringify(DONE))]);
  G = hydrate(win, 'regenerate');
  G._test.runBuild();
  await settle();
  win._popups[0].cb('cancel', win._popups[0].plan);
  await settle();
  ok(win._posts.length === 1, 'R2: cancel sends no second build POST');
  ok(/cancelled/.test(win.document.getElementById('gen-build-result').textContent),
    'R2: the panel says the build was cancelled');

  // ---- R4: a plain Generate posts neither field ---------------------------
  win = makeWorld([JSON.parse(JSON.stringify(DONE))]);
  G = hydrate(win, 'generate');
  G._test.runBuild();
  await settle();
  const b4 = win._posts[0] && win._posts[0].body;
  ok(b4 && b4.power_mode === null && b4.fsp_ack === null,
    'R4: Generate sends power_mode/fsp_ack null (got ' +
    JSON.stringify(b4 && { pm: b4.power_mode, ack: b4.fsp_ack }) + ')');

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('generate_regen_fsp_selfcheck: all checks passed');
})().catch(function (e) { console.error('threw', e && e.stack); process.exit(1); });
