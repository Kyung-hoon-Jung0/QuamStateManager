/* Behavioral check for the Chip Status HERO chip map (docs/92 P1) against the
 * REAL app.js + topo-graph.js + chip-status.js under jsdom.
 *
 * Pins the honesty contract + the reuse contract:
 *  - physical chips render the SVG map with the selected metric's VALUE text on
 *    every node (numbers stay ON this map — docs/91 §2.4), a gradient legend
 *    with a distinct "no data" swatch, and NO logical-layout note;
 *  - a grid-less chip with pairs still renders (connectivity layout) but wears
 *    TopoGraph.LOGICAL_LAYOUT_NOTE ON the map itself (docs/91 §2.1/§6.4);
 *  - no grid AND no pairs -> one honest line, no SVG, no fabricated raster;
 *  - coincident DECLARED cells (real 10Q chip: two qubits at "4,0") fan out —
 *    BOTH stones stay visible, marked shared;
 *  - edge colours come from the SAME _edgePaint as the card diagram
 *    (good >= 95% / none), metric switch re-renders + persists, a bad fit is
 *    ringed + struck through, and the card diagram below is still built.
 *
 * Run: node tests/chip_status_hero_selfcheck.cjs   (driven by tests/test_topology_hero.py)
 */
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
const read = (f) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', f), 'utf8');
const APP_JS = read('app.js');
const TOPO_JS = read('topo-graph.js');
const CS_JS = read('chip-status.js');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>'
    + '<div id="topo-hero"></div><div id="topo-html-wrap"></div>'
    + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () { win._htmxCalls.push(Array.prototype.slice.call(arguments)); } };
  win._htmxCalls = [];
  win.fetch = function () { return new win.Promise(function () {}); };
  // One Function scope so app.js's UI_CONFIG + topo-graph's window.TopoGraph are
  // visible to chip-status.js exactly as in the browser (<head> load order).
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  return win;
}

function mount(win, topo, findings) {
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, defaultThresholds: {},
                         diagFindings: findings || [], metricMeta: {} });
}

// ── 1) physical chip: map + values + legend, no logical note ────────────────
{
  const win = makeWorld();
  const topo = {
    nodes: [
      { id: 'qA1', grid_location: '0,0', T1: 2.4e-5, last_calibrated: Date.now() - 2 * 86400000 },
      { id: 'qA2', grid_location: '1,0', T1: 1.1e-5 },
      { id: 'qA3', grid_location: '0,1', T1: null },
      // a bad fit: raw present, gated value null, not unresolved
      { id: 'qA4', grid_location: '1,1', T1: -4.7e-5,
        metrics: { T1: { value: null, raw: -4.7e-5, unresolved: false } } },
    ],
    edges: [
      { pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1', has_cz: true, cz_fidelity: 0.97,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz' },
      { pair_id: 'qA3-qA4', source: 'qA3', target: 'qA4', has_cz: false, cz_fidelity: null,
        gate_kind: 'none', directed: false, active: null, best_gate: null },
    ],
  };
  mount(win, topo, [{ severity: 'error', jump_path: 'qubits.qA2.xy.thing', category: 'x', location: '', message: '' }]);

  const hero = win.document.getElementById('topo-hero');
  const svg = hero.querySelector('svg.topo-hero-svg');
  ok(!!svg, 'physical: hero SVG rendered');
  const stones = hero.querySelectorAll('.topo-hero-node');
  ok(stones.length === 4, 'physical: all 4 qubits drawn (got ' + stones.length + ')');
  ok(!hero.querySelector('.topo-hero-note'), 'physical: NO logical-layout note');

  // numbers ON the map: qA1's T1 value text is rendered
  const q1 = hero.querySelector('[data-hero-qubit="qA1"]');
  ok(q1 && /24\.0/.test(q1.textContent), 'physical: qA1 shows its T1 value on the map');
  const q3 = hero.querySelector('[data-hero-qubit="qA3"]');
  ok(q3 && q3.textContent.indexOf('—') !== -1, 'physical: missing T1 renders the honest dash');
  // bad fit: ringed + struck, never a heat colour
  const q4 = hero.querySelector('[data-hero-qubit="qA4"]');
  ok(q4 && q4.getAttribute('class').indexOf('hs-badfit') !== -1, 'physical: unphysical fit wears hs-badfit');

  // Edge colours come from the ONE _edgePaint. This used to be pinned as
  // "identical to the card diagram"; docs/120 item 11 deleted that diagram, so
  // the invariant is restated directly against the palette instead of against
  // a second renderer that no longer exists: a calibrated CZ takes the good
  // colour, an uncalibrated pair the no-data grey, and nothing else appears.
  const edges = hero.querySelectorAll('.topo-hero-edge');
  ok(edges.length === 2, 'physical: both edges drawn');
  const heroStrokes = Array.prototype.map.call(edges, function (g) {
    return g.querySelector('line').getAttribute('stroke');
  }).sort();
  const cardStrokes = ['#08519c', '#bbbbbb'];   // good-CZ, no-data
  ok(JSON.stringify(heroStrokes) === JSON.stringify(cardStrokes),
     'edge colours come from the one _edgePaint palette — hero '
     + JSON.stringify(heroStrokes) + ' vs cards ' + JSON.stringify(cardStrokes));
  ok(heroStrokes[0] !== heroStrokes[1],
     'physical: the 97% CZ edge and the no-data edge are visibly different');

  // legend: gradient + a distinct no-data swatch
  const legend = hero.querySelector('.topo-hero-legend');
  ok(!!legend && !!legend.querySelector('.topo-hero-lg-grad'), 'physical: continuous legend gradient present');
  ok(/no data/.test(legend.textContent), 'physical: legend names the no-data colour');

  // metric bar: T1 active by default; switching to Diagnostics re-renders,
  // persists, and colours qA2 (1 error finding) as fail
  const bar = hero.querySelector('.topo-hero-bar');
  ok(!!bar, 'physical: metric bar present');
  const t1btn = hero.querySelector('[data-hero-metric="T1"]');
  ok(t1btn && t1btn.className.indexOf('active') !== -1, 'physical: T1 selected by default');
  const diagBtn = hero.querySelector('[data-hero-metric="diag"]');
  ok(!!diagBtn, 'physical: Diagnostics metric offered');
  diagBtn.dispatchEvent(new win.Event('click', { bubbles: true }));
  ok(win.localStorage.getItem('quam_topo_hero_metric') === 'diag', 'metric switch persists to localStorage');
  const q2d = hero.querySelector('[data-hero-qubit="qA2"]');
  ok(q2d && q2d.getAttribute('class').indexOf('hs-fail') !== -1, 'diagnostics view: qA2 (1 error) wears hs-fail');
  const q1d = hero.querySelector('[data-hero-qubit="qA1"]');
  ok(q1d && q1d.getAttribute('class').indexOf('hs-pass') !== -1, 'diagnostics view: clean qubit wears hs-pass');

  // docs/120 item 11: there is exactly ONE chip map on this page now. The card
  // diagram that used to render below the hero is gone — that duplication is
  // what the customer reported ("the qubit layout appears twice").
  // The fixture deliberately still provides a #topo-html-wrap host: this is a
  // hostile control, so the assertion proves the CODE no longer builds cards
  // rather than merely proving the fixture stopped offering somewhere to put
  // them. (The template's own removal is pinned in test_topology_hero.py.)
  ok(win.document.querySelectorAll('.topo-node-card').length === 0,
     'the card diagram is GONE — one chip map, not two');
  ok((win.document.getElementById('topo-html-wrap') || { innerHTML: '' }).innerHTML === '',
     'nothing is rendered into the old card host even when it is present');

  // The qubit detail popup the cards used to OWN survives the deletion — that
  // is proved on its own timeline in hero_popup_selfcheck.cjs, because it opens
  // after a hover-intent delay and this file is synchronous.

  // single-click -> inspector (after the dbl-click window)
  const target = hero.querySelector('[data-hero-qubit="qA1"]');
  target.dispatchEvent(new win.Event('click', { bubbles: true }));
  setTimeout(function () {
    ok(win._htmxCalls.some(function (c) { return c[1] === '/qubit/qA1'; }),
       'node single-click opens the qubit inspector');
    part2();
  }, 550);
}

// ── 2) grid-less chip + pairs: logical layout, labelled ON the map ──────────
function part2() {
  const win = makeWorld();
  const topo = {
    nodes: [{ id: 'q1', T1: 1e-5 }, { id: 'q2', T1: 2e-5 }, { id: 'q3' }, { id: 'q4' }],
    edges: [
      { pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: false, cz_fidelity: null, gate_kind: 'none' },
      { pair_id: 'q2-3', source: 'q2', target: 'q3', has_cz: false, cz_fidelity: null, gate_kind: 'none' },
      { pair_id: 'q3-4', source: 'q3', target: 'q4', has_cz: false, cz_fidelity: null, gate_kind: 'none' },
    ],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');
  ok(!!hero.querySelector('svg.topo-hero-svg'), 'logical: map still drawn from connectivity');
  ok(hero.querySelectorAll('.topo-hero-node').length === 4, 'logical: every node placed');
  const note = hero.querySelector('.topo-hero-note');
  ok(!!note, 'logical: the layout note is ON the map');
  ok(note && note.textContent === win.TopoGraph.LOGICAL_LAYOUT_NOTE,
     'logical: note text IS TopoGraph.LOGICAL_LAYOUT_NOTE (one wording everywhere)');
  part3();
}

// ── 3) no grid AND no pairs: the honest line, never a fabricated raster ─────
function part3() {
  const win = makeWorld();
  mount(win, { nodes: [{ id: 'q1', T1: 1e-5 }, { id: 'q2' }], edges: [] }, []);
  const hero = win.document.getElementById('topo-hero');
  ok(!hero.querySelector('svg'), 'none: no SVG map is drawn');
  ok(/No chip map/.test(hero.textContent), 'none: the honest one-line message renders');
  ok(/declares no positions and no pairs/.test(hero.textContent), 'none: the message says WHY');
  part4();
}

// ── 4) coincident declared cells fan out — both stones visible ──────────────
function part4() {
  const win = makeWorld();
  const topo = {
    nodes: [
      { id: 'q2', grid_location: '4,0', T1: 1e-5 },
      { id: 'q10', grid_location: '4,0', T1: 2e-5 },
      { id: 'q1', grid_location: '0,0', T1: 3e-5 },
    ],
    edges: [],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');
  const a = hero.querySelector('[data-hero-qubit="q2"]');
  const b = hero.querySelector('[data-hero-qubit="q10"]');
  ok(!!a && !!b, 'coincident: BOTH declared-same-cell stones render');
  if (a && b) {
    ok(a.getAttribute('transform') !== b.getAttribute('transform'),
       'coincident: fanned apart (not hidden under each other)');
    ok(a.querySelector('circle').hasAttribute('stroke-dasharray'),
       'coincident: shared-cell members wear the dashed ring');
  }
  part5();
}

/* docs/126 ② — the metric patches drive the map: frequency patches, EDGE
 * metrics (2Q Bell / 2Q RB printed ON the edges, stones neutral), the 2×
 * default zoom with working controls, the fat edge hit area, and the pair
 * hover popup. */
function part5() {
  const win = makeWorld();
  const topo = {
    nodes: [
      { id: 'q1', grid_location: '0,0', T1: 1e-5, f_01: 4.8e9, readout_frequency: 7.2e9 },
      { id: 'q2', grid_location: '1,0', T1: 2e-5, f_01: 5.1e9, readout_frequency: 7.3e9 },
      { id: 'q3', grid_location: '0,1', T1: 3e-5, f_01: 4.9e9 },
    ],
    edges: [
      { pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz_flattop',
        gate_fidelities: [{ gate: 'cz_flattop', metric: 'StandardRB', value: 0.951 }],
        detuning: 2.5e8, has_coupler: true, coupler_decouple_offset: 0.012 },
      { pair_id: 'q1-3', source: 'q1', target: 'q3', has_cz: true, cz_fidelity: null,
        gate_kind: 'cz', directed: false, active: null, best_gate: null },
    ],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');
  const doc = win.document;

  const fbtn = hero.querySelector('[data-hero-metric="f_01"]');
  ok(!!fbtn, 'freq: Qubit freq patch offered');
  ok(fbtn && fbtn.className.indexOf('active') !== -1, 'freq: f_01 is the default metric');
  ok(!!hero.querySelector('[data-hero-metric="readout_frequency"]'),
     'freq: Readout freq patch offered');
  const q1n = hero.querySelector('[data-hero-qubit="q1"]');
  ok(q1n && /4\.8000 GHz/.test(q1n.textContent), 'freq: node shows its GHz value on the map');

  let svg = hero.querySelector('svg.topo-hero-svg');
  ok(svg && (svg.getAttribute('style') || '').indexOf('width:200%') !== -1,
     'zoom: default is 2x the pane fit (docs/126 "~2x bigger")');
  hero.querySelector('[data-hero-zoom="in"]').click();
  svg = hero.querySelector('svg.topo-hero-svg');
  ok(svg && (svg.getAttribute('style') || '').indexOf('width:225%') !== -1, 'zoom: + steps up');
  ok(win.localStorage.getItem('quam_topo_hero_zoom') === '2.25', 'zoom persists');
  hero.querySelector('[data-hero-zoom="fit"]').click();
  svg = hero.querySelector('svg.topo-hero-svg');
  ok(svg && (svg.getAttribute('style') || '').indexOf('width:100%') !== -1,
     'zoom: Fit returns to the pane fit');

  const eb = hero.querySelector('[data-hero-metric="rb2q_standard"]');
  ok(!!eb, 'edge metric: 2Q RB patch offered');
  ok(!!hero.querySelector('[data-hero-metric="cz_fidelity"]'), 'edge metric: 2Q Bell patch offered');
  eb.click();
  const evals = hero.querySelectorAll('.topo-hero-eval');
  ok(evals.length === 1, '2Q RB: exactly the measured edge prints a value (got '
     + evals.length + ')');
  ok(evals[0] && /95\.1/.test(evals[0].textContent), '2Q RB: the printed value is the best StandardRB');
  ok(hero.querySelectorAll('.topo-hero-node-neutral').length === 3,
     'edge mode: stones go neutral (edges carry the numbers)');
  ok(hero.querySelectorAll('.topo-hero-val').length === 0, 'edge mode: no stale node values');
  ok(win.localStorage.getItem('quam_topo_hero_metric') === 'rb2q_standard', 'edge metric persists');
  const legend = hero.querySelector('.topo-hero-legend');
  ok(legend && !!legend.querySelector('.topo-hero-lg-grad'), 'edge mode: gradient legend renders');

  const hit = hero.querySelector('.topo-hero-edge-hit');
  ok(hit && hit.getAttribute('stroke-width') === '22',
     'edges carry a fat (22) hit area — the customer could not click the old 11');

  const eg = hero.querySelector('[data-hero-pair="q1-2"]');
  eg.dispatchEvent(new win.Event('mouseenter'));
  setTimeout(function () {
    const pop = doc.querySelector('.topo-pair-popup');
    ok(!!pop, 'pair hover opens the pair popup');
    ok(pop && /q1-2/.test(pop.textContent), 'pair popup names the pair');
    ok(pop && /95\.10%/.test(pop.textContent), 'pair popup lists the per-gate RB fidelity');
    ok(pop && /detuning/.test(pop.textContent), 'pair popup lists the parameters section');
    finish();
  }, 400);
}

function finish() {
  if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
  console.log('chip_status_hero_selfcheck: all checks passed');
}
