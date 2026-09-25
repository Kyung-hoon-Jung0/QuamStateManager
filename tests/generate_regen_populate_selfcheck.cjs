/* jsdom behavioral check for the r16 regen populate-protect + scripts-path
 * wizard plumbing (docs/72).
 *
 * Pins:
 *  P1. hydrateFromSpec snapshots a DEEP COPY of spec.populate as the
 *      protect baseline and resets regenTouched.
 *  P2. autoApplyStandardDefaults is a NO-OP in regenerate mode (never
 *      fetches the builtin preset — synthetic defaults must not appear as
 *      chip values or taint the baseline diff).
 *  P3. applyLoAssignments in regen mode fills ONLY empty LO buckets; a
 *      {force:true} re-solve overwrites and records the cells as touched;
 *      the DOM refresh never clobbers a mid-typing (data-dirty) cell.
 *  P4. markPopulateTouched records only in regen mode.
 *  P5. Scripts export defaults ON; the scripts path FOLLOWS the output
 *      folder (<out>\state_gen_scripts) until the user types in the box.
 *  P6. The populate band column exists (qubit + resonator) and setPopValue
 *      coerces band to INT 1..3 (run_build's override gate).
 *
 * Run:  node tests/generate_regen_populate_selfcheck.cjs
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
  win.NumberInput = { fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win._fetchCount = 0;
  win.fetch = function () { win._fetchCount++; return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  return win;
}

const SPEC = {
  network: { host: '1.2.3.4', cluster_name: 'C', port: null },
  instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                 opx_plus: [], octaves: [] },
  qubits: ['q1', 'q2'],
  qubit_pairs: [['q1', 'q2']],
  twpas: [],
  lines: [],
  pair_gate: 'cz_tunable',
  populate: { qubit: { q1: { RF_freq: 5.1e9, LO_frequency: 5.0e9 } } }
};

// ---- P1: baseline snapshot is a deep copy ---------------------------------
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const spec = JSON.parse(JSON.stringify(SPEC));
  G.hydrateFromSpec(spec, { mode: 'regenerate' });
  const st = G._test.state;
  ok(st.regenBaselinePopulate.qubit.q1.RF_freq === 5.1e9,
     'P1: baseline captured from hydrated spec');
  spec.populate.qubit.q1.RF_freq = 9e9;             // mutate the live spec
  st.spec.populate.qubit.q1.RF_freq = 9e9;
  ok(st.regenBaselinePopulate.qubit.q1.RF_freq === 5.1e9,
     'P1: baseline is a DEEP COPY (later edits do not rewrite it)');
  ok(Object.keys(st.regenTouched || {}).length === 0, 'P1: touched reset');
})();

// ---- P2: autoApplyStandardDefaults no-ops in regen ------------------------
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });
  const before = win._fetchCount;
  G._test.autoApplyStandardDefaults();
  ok(win._fetchCount === before,
     'P2: regen mode never fetches the builtin standard preset');
  // QA generate-r2-05: the one-shot flag became a per-row record; regen
  // mode must not start one either.
  ok(G._test.state.autoPresetRows == null,
     'P2: no prefill record is started in regen mode');
})();

// ---- P3: applyLoAssignments fill-only-empty / force+touched / dirty-skip --
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });
  const st = G._test.state;
  st.spec.populate.qubit.q2 = {};                    // q2 has NO LO
  G._test.applyLoAssignments({ 'qubit/q1': 6.0e9, 'qubit/q2': 6.0e9 });
  ok(st.spec.populate.qubit.q1.LO_frequency === 5.0e9,
     'P3: existing (chip-real) LO not overwritten in regen mode');
  ok(st.spec.populate.qubit.q2.LO_frequency === 6.0e9,
     'P3: empty LO bucket IS filled');

  G._test.applyLoAssignments({ 'qubit/q1': 6.2e9 }, { force: true });
  ok(st.spec.populate.qubit.q1.LO_frequency === 6.2e9,
     'P3: force re-solve overwrites');
  ok(st.regenTouched['qubit|q1|LO_frequency'] === 1,
     'P3: force re-solve marks the cell touched');

  // dirty-cell DOM skip: a mid-typing LO input keeps the typed text
  const inp = win.document.createElement('input');
  inp.className = 'gen-pop-in';
  inp.dataset.field = 'LO_frequency';
  inp.dataset.group = 'qubit';
  inp.dataset.rid = 'q1';
  inp.dataset.dirty = '1';
  inp.value = 'typing…';
  win.document.body.appendChild(inp);
  G._test.applyLoAssignments({ 'qubit/q1': 6.4e9 }, { force: true });
  ok(inp.value === 'typing…', 'P3: data-dirty cell never clobbered mid-typing');
})();

// ---- P4: markPopulateTouched is regen-only --------------------------------
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const st = G._test.state;
  st.mode = 'generate';
  G._test.markPopulateTouched('qubit', 'q1', 'RF_freq');
  ok(Object.keys(st.regenTouched || {}).length === 0,
     'P4: generate mode records nothing');
  st.mode = 'regenerate';
  st.regenTouched = {};
  G._test.markPopulateTouched('qubit', 'q1', 'RF_freq');
  ok(st.regenTouched['qubit|q1|RF_freq'] === 1, 'P4: regen mode records');
})();

// ---- P5: scripts default ON + follow-path ---------------------------------
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const st = G._test.state;
  ok(st.scriptsEnabled === true, 'P5: scripts export defaults ON');
  ok(G._test.autoScriptsPath('D:\\quam_states\\labd\\17Q') ===
     'D:\\quam_states\\labd\\17Q\\state_gen_scripts',
     'P5: windows join');
  ok(G._test.autoScriptsPath('/data/chips/17Q/') ===
     '/data/chips/17Q/state_gen_scripts', 'P5: posix join + trailing slash');

  const out = win.document.getElementById('gen-output-path');
  const sp = win.document.getElementById('gen-scripts-path');
  out.value = 'D:\\quam_states\\labd\\17Q_20260802';
  out.dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(sp.value === 'D:\\quam_states\\labd\\17Q_20260802\\state_gen_scripts',
     'P5: scripts path follows the output folder');

  sp.value = 'D:\\custom\\scripts';
  sp.dispatchEvent(new win.Event('input', { bubbles: true }));
  out.value = 'D:\\quam_states\\labd\\other';
  out.dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(st.scriptsPath === 'D:\\custom\\scripts',
     'P5: a user-typed scripts path stops the follow');
})();

// ---- P6: band column + int coercion ---------------------------------------
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  const qb = G._test.POP_QUBIT_COLS.filter(function (c) { return c.field === 'band'; });
  const rb = G._test.POP_RESONATOR_COLS.filter(function (c) { return c.field === 'band'; });
  ok(qb.length === 1 && qb[0].kind === 'select', 'P6: qubit band column exists');
  ok(rb.length === 1, 'P6: resonator band column exists');
  const bucket = {};
  G._test.setPopValue(bucket, qb[0], '3', 'qubit', 'q1');
  ok(bucket.band === 3, 'P6: band stored as INT');
  G._test.setPopValue(bucket, qb[0], '', 'qubit', 'q1');
  ok(!('band' in bucket), 'P6: empty clears (auto)');
  G._test.setPopValue(bucket, qb[0], '9', 'qubit', 'q1');
  ok(!('band' in bucket), 'P6: out-of-range rejected');
})();

// ---- P7 (r16 0-1): prunePopulate keeps short-form pair keys ---------------
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const st = G._test.state;
  st.spec.populate = { pairs: {
    'q1-2':   { cz_amplitude: 0.1 },     // short second member — must SURVIVE
    'qA1-A2': { cz_amplitude: 0.2 },     // short letter form — must SURVIVE
    'q1-q9':  { cz_amplitude: 0.3 }      // truly dead member — must go
  } };
  G._test.prunePopulate({ q1: true, q2: true, qA1: true, qA2: true });
  const keys = Object.keys(st.spec.populate.pairs);
  const okShort = keys.indexOf('q1-2') >= 0 && keys.indexOf('qA1-A2') >= 0;
  const deadGone = keys.indexOf('q1-q9') < 0;
  if (!okShort) { console.error('FAIL: P7: short-form pair populate keys deleted'); fails++; }
  if (!deadGone) { console.error('FAIL: P7: dead pair key survived'); fails++; }
})();

// ---- P8 (QA regenerate-r2-03): fill-empty preset Apply never protects ------
// A cell the extractor could not read back displays blank although the chip
// holds a calibration there; protecting the preset's fill made the merge keep
// 0.1 over the chip's CZ amp. Only Overwrite ON records touched cells.
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });
  const st = G._test.state;
  const preset = { sections: { qubit: { defaults: { anharmonicity: 2e8 } } } };
  const rep = G._test.applyPreset(preset, false);
  ok(rep.applied === 2 && st.spec.populate.qubit.q2.anharmonicity === 2e8,
     'P8: fill-empty still writes the blank cells');
  ok(Object.keys(st.regenTouched || {}).length === 0,
     'P8: fill-empty preset Apply marks NO cell touched in regen mode');
  // Review of r2-03: the fill is recorded APART and shipped with the build,
  // so the server can land it where the source leaf is null (a null
  // anharmonicity) and never over a number. Unrecorded, the null stayed.
  ok(st.regenFilled && st.regenFilled['qubit|q1|anharmonicity'] === 1 &&
     st.regenFilled['qubit|q2|anharmonicity'] === 1,
     'P8: fill-empty preset Apply records the filled cells (got ' +
     JSON.stringify(st.regenFilled) + ')');
  // A cell step 6 SHOWED the chip's value for, then the user cleared, still
  // means that value (clear = keep, tier-1 carries it) — fill-empty leaves
  // it blank instead of showing a value the build would not write.
  delete st.spec.populate.qubit.q1.RF_freq;
  const rep2 = G._test.applyPreset(
    { sections: { qubit: { defaults: { RF_freq: 6e9 } } } }, false);
  ok(st.spec.populate.qubit.q1.RF_freq === undefined &&
     st.spec.populate.qubit.q2.RF_freq === 6e9 && rep2.applied === 1 &&
     !st.regenFilled['qubit|q1|RF_freq'],
     'P8: fill-empty skips a cleared chip value, fills a truly empty cell (got q1 ' +
     st.spec.populate.qubit.q1.RF_freq + ', q2 ' + st.spec.populate.qubit.q2.RF_freq +
     ', applied ' + rep2.applied + ')');
  let posted = null;
  // A synchronous thenable, so runBuild's select-env round-trip re-enters
  // it inside this (sync) block and the build POST is captured here.
  function sync(v) {
    if (v && typeof v.then === 'function') return v;
    return { then(f) { return sync(f ? f(v) : v); }, catch() { return this; } };
  }
  win.fetch = function (url, opts) {
    if (/select-env/.test(String(url))) {
      return sync({ json() { return sync({ ok: true }); } });
    }
    if (/\/(re)?generate\/build/.test(String(url))) posted = JSON.parse(opts.body);
    return new win.Promise(function () {});
  };
  st.env = 'C:/py/python.exe';
  win.document.getElementById('gen-output-path').value = 'D:\\out\\chip';
  // gen-session (QA regenerate-r2-18) refuses a ticked scripts export with no
  // folder; give it the folder the output path implies (merged at integration).
  win.document.getElementById('gen-scripts-path').value = 'D:\\out\\chip\\state_gen_scripts';
  G._test.runBuild();
  const pf = (posted && posted.populate_filled || []).map(function (c) { return c.join('|'); });
  ok(pf.indexOf('qubit|q1|anharmonicity') >= 0 && pf.indexOf('qubit|q2|anharmonicity') >= 0,
     'P8: the regen build POST carries populate_filled (got ' +
     JSON.stringify(posted && posted.populate_filled) + ')');
  G._test.applyPreset({ sections: { qubit: { defaults: { anharmonicity: 3e8 } } } }, true);
  ok(st.regenTouched['qubit|q1|anharmonicity'] === 1 &&
     st.regenTouched['qubit|q2|anharmonicity'] === 1,
     'P8: Overwrite ON records the cells touched');
})();

// ---- P9 (QA regenerate-r2-31): the step-6 intro says what a BLANK cell
// means in THIS mode — regen: the source chip's value (tier-1 carry), not
// "build_quam's defaults"; Start over flips it back. -------------------------
(function () {
  function introShown(win) {
    const spans = win.document.querySelectorAll('#gen-pop-intro [data-intro-mode]');
    const out = {};
    spans.forEach(function (s) { out[s.dataset.introMode] = !s.hidden; });
    return out;
  }
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'regenerate' });
  G.goToStep(6);
  let s = introShown(win);
  ok(s.regenerate === true && s.generate === false,
     'P9: regen step 6 shows the source-chip sentence only (got ' + JSON.stringify(s) + ')');
  const txt = win.document.getElementById('gen-pop-intro').textContent;
  ok(/source\s+chip's value/.test(txt), 'P9: the regen sentence names the source chip');
  win.document.getElementById('gen-reset').click();     // Start over → generate
  G.goToStep(6);
  s = introShown(win);
  ok(s.generate === true && s.regenerate === false,
     'P9: after Start over the generate sentence is back (got ' + JSON.stringify(s) + ')');
  const win2 = makeWorld();
  win2.QuamGen.init();
  win2.QuamGen.goToStep(6);
  s = introShown(win2);
  ok(s.generate === true && s.regenerate === false, 'P9: a generate mount shows build_quam');
})();

// ---- P10 (QA F19): an LO cell rewritten by applyLoAssignments is regrouped
// like every other numeric cell — MHz showed RF "7,100" beside LO "7275". ----
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const st = G._test.state;
  st.populateUnits.freq = 'MHz';
  // A grouping NumberInput.format (the real one groups on blur/format()).
  win.NumberInput.format = function (el) {
    const m = /^(-?)(\d+)(\.\d+)?$/.exec(String(el.value));
    if (m) el.value = m[1] + m[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (m[3] || '');
  };
  const inp = win.document.createElement('input');
  inp.className = 'gen-pop-in';
  inp.dataset.field = 'LO_frequency';
  inp.dataset.group = 'qubit';
  inp.dataset.rid = 'q1';
  win.document.body.appendChild(inp);
  G._test.applyLoAssignments({ 'qubit/q1': 7.275e9 });
  ok(inp.value === '7,275', 'P10: LO shows "7,275" in MHz (got "' + inp.value + '")');
})();

// ---- P11 (QA generate-r2-21): Explorer "Copy as path" quotes are stripped ---
(function () {
  const win = makeWorld();
  const G = win.QuamGen;
  G.init();
  const st = G._test.state;
  const U = G._test.unquotePath;
  ok(U('"D:\\a\\b"') === 'D:\\a\\b', 'P11: one matched double-quote pair stripped');
  ok(U("  'D:\\a'  ") === 'D:\\a', 'P11: single quotes + outer space');
  ok(U('C:\\a\\b"c') === 'C:\\a\\b"c', 'P11: an interior quote is left alone');
  ok(U('"D:\\a') === '"D:\\a', 'P11: an unmatched quote is left alone');

  const out = win.document.getElementById('gen-output-path');
  const sp = win.document.getElementById('gen-scripts-path');
  out.value = '"D:\\gen_out\\r2_quoted"';
  out.dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(st.outputPath === 'D:\\gen_out\\r2_quoted', 'P11: the output folder is stored unquoted');
  ok(sp.value === 'D:\\gen_out\\r2_quoted\\state_gen_scripts',
     'P11: the scripts folder follows the UNQUOTED path — got ' + sp.value);
  out.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(out.value === 'D:\\gen_out\\r2_quoted', 'P11: on commit the box shows the path used');

  // the real step-7 Next gate accepts it
  out.value = '"D:\\gen_out\\r2_quoted"';
  G.goToStep(7);
  const msg = win.document.getElementById('gen-message');
  G.tryNext();
  ok(G.state.step === 8, 'P11: a quoted absolute output passes step 7 — got step ' +
     G.state.step + ' / ' + (msg.hidden ? '' : msg.textContent));
  // ...and so does a quoted scripts folder the user pasted themselves
  sp.value = '"D:\\gen_out\\my scripts"';
  sp.dispatchEvent(new win.Event('input', { bubbles: true }));
  sp.value = '"D:\\gen_out\\my scripts"';      // the box as typed, quotes and all
  G.goToStep(7);
  G.tryNext();
  ok(G.state.step === 8 && st.scriptsPath === 'D:\\gen_out\\my scripts',
     'P11: a quoted scripts folder passes step 7 unquoted — got step ' + G.state.step +
     ' / ' + (msg.hidden ? '' : msg.textContent));

  // the custom interpreter is probed unquoted
  const urls = [];
  win.fetch = function (u) { urls.push(String(u)); return new win.Promise(function () {}); };
  const ci = win.document.getElementById('gen-env-custom-path');
  ci.value = '"D:\\miniconda3\\envs\\cqt\\python.exe"';
  G.useCustomEnv();
  const want = '/generate/probe?python=' +
    encodeURIComponent('D:\\miniconda3\\envs\\cqt\\python.exe');
  ok(urls.indexOf(want) >= 0, 'P11: the interpreter is probed without its quotes — got ' +
     JSON.stringify(urls));
})();

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('generate_regen_populate_selfcheck: all checks passed');
