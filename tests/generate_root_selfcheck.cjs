// docs/176 — the four Generate-wizard defects found while trying to build the
// KRISS 5Q chip in the environment that lab actually runs (conda KRISS_CZ,
// which imports their own quam_config).
//
// Every one of these was measured in a real browser first; this harness
// EXECUTES the shipped renderers rather than grepping them, because a
// source-only pin in this project has twice survived an `if (false)`.
//
//  R1  a STRING blocker renders as the sentence it is. Two shapes arrive under
//      the one `capability_blockers` key — `capabilities.assess` sends objects
//      {label, package, symbol, fix}, the ROOT-CLASS refusal sends one
//      finished sentence — and the object template printed
//      "• undefined — needs ? · (missing). Fix:", throwing away the only text
//      that said what was wrong.
//  R2  the verdict cannot contradict the list beneath it. `buildable` means
//      "nothing BLOCKS it"; the page said "can build everything this chip
//      needs" directly above a list headed "Will be skipped / downgraded", one
//      of whose entries loses a qubit's flux component entirely.
//  R3  a root refusal is a BLOCKER in the review, not a surprise in the build's
//      400 half a minute later.
//  R4  the wizard can NAME the chip's root class, and "Automatic" is
//      byte-for-byte the old behaviour — the key reaches the spec only when a
//      person picks one.
//
// Run: node tests/generate_root_selfcheck.cjs   (needs jsdom)
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

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

// What the server answers. Each section swaps in its own body.
let CAP_BODY = null;

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  // Bridge every global the code reads BARE (the standing rule since docs/78).
  win.NumberInput = {
    fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} },
    format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.WiringGrid = null;
  win.confirm = function () { return true; };
  // Route by URL. A mock that answers EVERY endpoint with the capability body
  // is not a quieter harness, it is a noisier one: the wizard's env handlers
  // read that body too and cleared `state.env`, so the second render bailed at
  // its first line with an empty box and no error anywhere.
  win.fetch = function (url) {
    if (String(url).indexOf('/generate/capabilities') >= 0) {
      return win.Promise.resolve({
        ok: true, status: 200,
        json: function () { return win.Promise.resolve(CAP_BODY); }
      });
    }
    return new win.Promise(function () {});
  };
  new win.Function(GEN_JS).call(win);
  return win;
}

const win = makeWorld();
const G = win.QuamGen;
const T = G._test;
const doc = win.document;

const SPEC = {
  network: { host: '1.2.3.4', cluster_name: 'C' },
  instruments: {
    controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }] }],
    opx_plus: [], octaves: []
  },
  qubits: ['q1', 'q2'], qubit_pairs: [], twpas: [], pair_gate: '',
  lines: [], populate: { qubits: {}, pairs: {} }
};

function reset() {
  G.hydrateFromSpec(JSON.parse(JSON.stringify(SPEC)), { mode: 'generate' });
  G.state.env = 'py';                       // the report bails without one
  return G.state;
}

// The renderer is a promise chain; one macrotask turn settles it.
function settle() { return new Promise(function (r) { setTimeout(r, 0); }); }

function capBody(over) {
  const base = {
    ok: true, probe_ok: true, cached: false,
    report: {
      manifest_ok: true, buildable: true, blockers: [], warnings: [], inventory: []
    },
    flavor: [],
    root: { needed: false, chosen: null, blocker: null, candidates: [] },
    roots: []
  };
  return Object.assign(base, over || {});
}

const ROOTS = [
  {
    path: 'quam_config.my_quam.Quam', importable: true, holds_qdac: true,
    qubits_type: 'typing.Dict[str, quam_config.my_quam.AnyTransmon]'
  },
  {
    path: 'quam_builder.architecture.superconducting.qpu.flux_tunable_quam.FluxTunableQuam',
    importable: true, holds_qdac: false,
    qubits_type: 'typing.Dict[str, quam_builder.FluxTunableTransmon]'
  }
];

async function main() {
  // jsdom fires DOMContentLoaded asynchronously, and the wizard's own init()
  // listens for it and resets the draft (env -> null). The other generate
  // harnesses are synchronous and finish before it ever runs; this one awaits,
  // so it has to let init() go first or the second render bails at its first
  // line with an empty box and no error anywhere.
  await settle();

  const box = doc.createElement('div');
  doc.body.appendChild(box);

  // ── R1: a string blocker is a sentence, not "undefined" ────────────────────
  reset();
  const SENTENCE = 'No QPU root class in this environment can hold QDAC-biased qubits.';
  T.showBuildResult({ ok: false, capability_blockers: [SENTENCE] }, null);
  let lines = Array.prototype.map.call(
    doc.querySelectorAll('#gen-build-result .gen-build-err-line'),
    function (p) { return p.textContent; });
  ok(lines.length === 1, 'R1: the one blocker renders as one line');
  ok(lines[0] === '• ' + SENTENCE,
    'R1: a STRING blocker renders verbatim — got ' + JSON.stringify(lines[0]));
  ok(lines[0].indexOf('undefined') < 0 && lines[0].indexOf('(missing)') < 0,
    'R1: and never through the object template');

  // …and the OBJECT shape still renders as it always did.
  T.showBuildResult({
    ok: false,
    capability_blockers: [{
      label: 'TWPA lines', package: 'quam_builder', symbol: 'add_twpa_lines',
      fix: 'upgrade quam_builder'
    }]
  }, null);
  lines = Array.prototype.map.call(
    doc.querySelectorAll('#gen-build-result .gen-build-err-line'),
    function (p) { return p.textContent; });
  ok(/TWPA lines .* needs quam_builder .* add_twpa_lines \(missing\)\. Fix: upgrade quam_builder/
    .test(lines[0]),
    'R1: the object shape is unchanged — got ' + JSON.stringify(lines[0]));

  // ── R2: the verdict never contradicts the list under it ───────────────────
  reset();
  CAP_BODY = capBody({
    report: {
      manifest_ok: true, buildable: true, blockers: [],
      warnings: [{
        id: 'w1', label: 'QDAC bias',
        detail: 'the qubit loses its flux component'
      }],
      inventory: []
    }
  });
  T.renderCapabilityReport(box);
  await settle();
  let head = box.querySelector('.gen-cap-head').textContent;
  ok(head.indexOf('everything') < 0,
    'R2: with skips listed, the verdict does not claim "everything" — ' + head);
  ok(/Nothing blocks the build/.test(head) && /1 thing will be skipped/.test(head),
    'R2: it says what is true instead — ' + head);
  ok(!!box.querySelector('.gen-cap-degrade'),
    'R2: and the skip list is still rendered beneath it');

  // With nothing skipped, "everything" is honest and stays.
  CAP_BODY = capBody();
  T.renderCapabilityReport(box);
  await settle();
  head = box.querySelector('.gen-cap-head').textContent;
  ok(/can build everything this chip needs/.test(head),
    'R2: a clean env still reads "everything" — ' + head);

  // ── R3: the root refusal is a blocker in the REVIEW ───────────────────────
  const BLOCK = 'No QPU root class in this environment can hold QDAC-biased qubits.';
  CAP_BODY = capBody({
    root: { needed: true, chosen: null, blocker: BLOCK, candidates: [] },
    roots: ROOTS
  });
  T.renderCapabilityReport(box);
  await settle();
  head = box.querySelector('.gen-cap-head').textContent;
  ok(head.charAt(0) === '✗',
    'R3: a root refusal makes the verdict negative even with buildable:true — ' + head);
  const rb = Array.prototype.map.call(box.querySelectorAll('.gen-cap-blocker'),
    function (p) { return p.textContent; });
  ok(rb.some(function (t) { return t.indexOf(BLOCK) >= 0; }),
    'R3: and the refusal itself is shown, worded by the server');

  // ── R4: the wizard can name the root class ────────────────────────────────
  let sel = box.querySelector('#gen-quam-class');
  ok(!!sel, 'R4: the picker renders when the env offers roots');
  ok(sel.options[0].value === '' && /Automatic/.test(sel.options[0].textContent),
    'R4: Automatic is the first option and carries no value');
  ok(sel.value === '', 'R4: and is what an untouched spec shows');
  ok(!('quam_class' in G.state.spec),
    "R4: rendering the picker puts NOTHING on the spec (today's behaviour)");
  ok(sel.options.length === 1 + ROOTS.length, 'R4: every importable root is offered');
  ok(sel.options[1].value === 'quam_config.my_quam.Quam',
    "R4: the lab's own root is offered first, as the probe reports it");
  ok(/holds AnyTransmon/.test(sel.options[1].textContent),
    'R4: each option says what it can hold — ' + sel.options[1].textContent);

  // A person picks one: the key reaches the spec and the review re-runs.
  sel.value = 'quam_config.my_quam.Quam';
  sel.dispatchEvent(new win.Event('change'));
  ok(G.state.spec.quam_class === 'quam_config.my_quam.Quam',
    'R4: a pick lands on the spec');
  await settle();
  sel = box.querySelector('#gen-quam-class');
  ok(!!sel && sel.value === 'quam_config.my_quam.Quam',
    'R4: and the re-rendered picker shows it');

  // …and going back to Automatic REMOVES it, rather than writing "".
  sel.value = '';
  sel.dispatchEvent(new win.Event('change'));
  ok(!('quam_class' in G.state.spec),
    'R4: Automatic deletes the key — an empty string is not the same spec');

  // No roots reported (an unprobed env) ⇒ no picker at all, never an empty one.
  CAP_BODY = capBody({ roots: [] });
  T.renderCapabilityReport(box);
  await settle();
  ok(!box.querySelector('#gen-quam-class'),
    'R4: an env that reports no roots gets no picker');

  console.log(fails ? 'FAILED (' + fails + ')'
    : 'generate_root_selfcheck ok (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
}

main().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
