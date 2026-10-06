/* jsdom behavioral check: a qubit renamed on the Re-generate wizard keeps
 * its identity all the way to the build POST.
 *
 * Pins:
 *  N1. hydrate starts every row as its own source qubit.
 *  N2. a rename (naming-scheme map or one row) moves the source record, the
 *      populate-protect baseline and the touched / filled cells with the row,
 *      pairs included.
 *  N3. a deleted row's leftover record never shadows a qubit renamed onto
 *      its id.
 *  N4. the build POST sends {current id: source id} for the qubits the build
 *      has, and null outside Re-generate.
 *  N5. the result panel names each renamed qubit and pair.
 *
 * Run:  node tests/generate_regen_rename_selfcheck.cjs
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
function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }
async function settle() { for (let i = 0; i < 15; i++) await tick(); }
const J = JSON.stringify;

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
      return reply((buildReplies || []).shift() || { ok: false, error: 'no more replies' });
    }
    return new win.Promise(function () {});
  };
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
    qubits: ['q1', 'q2', 'q3'], qubit_pairs: [['q1', 'q2'], ['q2', 'q3']],
    twpas: [], lines: [], pair_gate: 'cz_tunable',
    populate: { qubit: { q1: { anharmonicity: -200e6 }, q2: { anharmonicity: -210e6 },
                         q3: { anharmonicity: -220e6 } },
                pairs: { 'q1-q2': { cz_amp: 0.1 }, 'q2-q3': { cz_amp: 0.2 } } }
  }, { mode: mode, buildEndpoint: mode === 'regenerate' ? '/regenerate/build' : undefined,
       sourcePath: 'D:\\src' });
  G._test.state.env = 'C:/py/python.exe';
  win.document.getElementById('gen-output-path').value = 'D:\\out\\chip';
  win.document.getElementById('gen-scripts-path').value = 'D:\\out\\chip\\state_gen_scripts';
  return G;
}

const DONE = { ok: true, status: 'ok', result: { qubits: ['q0', 'q1', 'q2'], qubit_pairs: [] },
  merge: { carried: 10, grafted: 0, populate_protected: 0, populate_conflicts: [],
           qubits_renamed: [{ old: 'q1', new: 'q0' }, { old: 'q2', new: 'q1' },
                            { old: 'q3', new: 'q2' }],
           pairs_renamed: [{ old: 'q1-q2', new: 'q0-q1' }] } };

(async function () {
  // ---- N1 + N2: a scheme-style map moves every record with its row -------
  let win = makeWorld([J(DONE)].map(JSON.parse));
  let G = hydrate(win, 'regenerate');
  let st = G._test.state;
  ok(J(st.regenQubitSource) === J({ q1: 'q1', q2: 'q2', q3: 'q3' }),
     'N1: every row starts as its own source (got ' + J(st.regenQubitSource) + ')');
  // hydration normalizes populate.pairs; the pair records are what matter here
  st.regenBaselinePopulate.pairs = { 'q1-q2': { cz_amp: 0.1 }, 'q2-q3': { cz_amp: 0.2 } };
  G._test.markPopulateTouched('qubit', 'q2', 'anharmonicity');
  G._test.markPopulateTouched('pairs', 'q2-q3', 'cz_amp');
  G._test.applyQubitIdMap({ q1: 'q0', q2: 'q1', q3: 'q2' });
  ok(J(st.regenQubitSource) === J({ q0: 'q1', q1: 'q2', q2: 'q3' }),
     'N2: the source record follows the rename (got ' + J(st.regenQubitSource) + ')');
  const bq = st.regenBaselinePopulate.qubit;
  ok(bq.q0.anharmonicity === -200e6 && bq.q1.anharmonicity === -210e6 &&
     bq.q2.anharmonicity === -220e6,
     'N2: the protect baseline follows the rename (got ' + J(bq) + ')');
  ok(J(Object.keys(st.regenBaselinePopulate.pairs).sort()) === J(['q0-q1', 'q1-q2']) &&
     st.regenBaselinePopulate.pairs['q0-q1'].cz_amp === 0.1,
     'N2: baseline pair keys follow (got ' + J(st.regenBaselinePopulate.pairs) + ')');
  ok(st.regenTouched['qubit|q1|anharmonicity'] === 1 &&
     st.regenTouched['pairs|q1-q2|cz_amp'] === 1 &&
     Object.keys(st.regenTouched).length === 2,
     'N2: touched cells follow (got ' + J(st.regenTouched) + ')');

  // ---- N4 + N5: the build POST and the result panel ----------------------
  G._test.runBuild();
  await settle();
  const body = win._posts[0] && win._posts[0].body;
  ok(body && J(body.qubit_sources) === J({ q0: 'q1', q1: 'q2', q2: 'q3' }),
     'N4: the build sends the record (got ' + J(body && body.qubit_sources) + ')');
  const res = win.document.getElementById('gen-build-result').textContent;
  ok(/Renamed q1 → q0, q2 → q1, q3 → q2/.test(res),
     'N5: the result names the renamed qubits (got ' + res.slice(0, 400) + ')');
  ok(/Pairs renamed with them: q1-q2 → q0-q1/.test(res),
     'N5: ...and the pairs (got ' + res.slice(0, 400) + ')');

  // ---- N3: a deleted row's leftover never shadows a renamed one ---------
  win = makeWorld([]);
  G = hydrate(win, 'regenerate');
  st = G._test.state;
  st.spec.qubits.splice(1, 1);                       // q2 deleted (its record stays)
  G._test.renameQubit('q3', 'q2');
  ok(st.regenQubitSource.q2 === 'q3',
     'N3: q2 is the old q3 now (got ' + J(st.regenQubitSource) + ')');
  G._test.runBuild();
  await settle();
  const b3 = win._posts[0] && win._posts[0].body;
  ok(b3 && J(b3.qubit_sources) === J({ q1: 'q1', q2: 'q3' }),
     'N4: only the qubits the build has are sent (got ' + J(b3 && b3.qubit_sources) + ')');

  // ---- N4: outside Re-generate nothing is recorded or sent ---------------
  win = makeWorld([]);
  G = hydrate(win, 'generate');
  st = G._test.state;
  G._test.applyQubitIdMap({ q1: 'q0' });
  ok(st.regenQubitSource == null, 'N4: Generate keeps no source record');
  G._test.runBuild();
  await settle();
  const b4 = win._posts[0] && win._posts[0].body;
  ok(b4 && b4.qubit_sources === null,
     'N4: Generate sends qubit_sources null (got ' + J(b4 && b4.qubit_sources) + ')');

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('generate_regen_rename_selfcheck: all checks passed');
})().catch(function (e) { console.error('threw', e && e.stack); process.exit(1); });
