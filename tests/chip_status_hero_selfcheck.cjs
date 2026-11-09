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
      { pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.97,
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
  ok(win.localStorage.getItem('quam_topo_hero_metric_v2') === 'diag', 'metric switch persists to localStorage');
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
        gate_fidelities: [
          { gate: 'cz_flattop', metric: 'StandardRB', value: 0.951 },
          { gate: 'cz_gaussian_bipolar', metric: 'StandardRB', value: 0.902 },
        ],
        detuning: -0.16586175268952874, has_coupler: true, coupler_decouple_offset: 0.012 },
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

  const svg = hero.querySelector('svg.topo-hero-svg');
  ok(svg && (svg.getAttribute('style') || '').indexOf('width:170%') !== -1,
     'zoom: default is 1.7x the old render (customer dialed 2x back)');
  hero.querySelector('[data-hero-zoom="in"]').click();
  // zoom now applies IN PLACE (a rebuild would destroy the slider mid-drag)
  ok(svg.style.width === '195%', 'zoom: + steps up in place (' + svg.style.width + ')');
  ok(Math.abs(parseFloat(win.localStorage.getItem('quam_topo_hero_zoom')) - 1.95) < 1e-9,
     'zoom persists');
  hero.querySelector('[data-hero-zoom="fit"]').click();
  ok(svg.style.width === '100%', 'zoom: Fit returns to the pane fit');
  ok(!!hero.querySelector('.topo-hero-zslider'), 'zoom: the slider renders');

  // docs/126 compact mode: smaller stones, hero-compact class for the bigger
  // relative fonts; persisted.
  hero.querySelector('[data-hero-compact]').click();
  const csvg = hero.querySelector('svg.topo-hero-svg');
  ok(csvg.classList.contains('hero-compact'), 'compact: class applied');
  ok(csvg.querySelector('.topo-hero-stone').getAttribute('r') === '33',
     'compact: stones shrink to r=33');
  ok(win.localStorage.getItem('quam_topo_hero_compact') === '1', 'compact persists');
  hero.querySelector('[data-hero-compact]').click();
  ok(hero.querySelector('svg.topo-hero-svg .topo-hero-stone').getAttribute('r') === '37',
     'compact: toggling back restores r=37');

  const eb = hero.querySelector('[data-hero-metric="rb2q_standard"]');
  ok(!!eb, 'edge metric: 2Q Clifford (SRB) patch offered');
  // One source per patch: the mixed-source cz_fidelity patch is retired.
  ok(!hero.querySelector('[data-hero-metric="cz_fidelity"]'), 'edge metric: no mixed-source 2Q gate fid. patch');
  ok(!!hero.querySelector('[data-hero-metric="bell_state"]'), 'edge metric: Bell state fid. patch offered');
  // docs/138 — the Clifford patch must SAY it is per-Clifford. It used to read
  // "2Q RB", indistinguishable from the interleaved gate number beside it.
  ok(/Clifford/.test(eb.textContent), 'the Clifford patch names itself (' + eb.textContent + ')');
  eb.click();
  const evals = hero.querySelectorAll('.topo-hero-eval');
  ok(evals.length === 1, '2Q RB: exactly the measured edge prints a value (got '
     + evals.length + ')');
  ok(evals[0] && /95\.1/.test(evals[0].textContent), '2Q RB: the printed value is the best StandardRB');
  // docs/126: the value text draws AFTER the role markers (they used to cover
  // it dead-center on the real chip), and the markers half-overlap the stones
  const role = hero.querySelector('.cm-role rect');
  ok(role && (evals[0].compareDocumentPosition(role) & 2) !== 0,
     'edge values draw ON TOP of the C/T/M markers');
  // per-gate toggle: two gates ship StandardRB → the pulse bar renders,
  // picking the second gate switches the printed number
  const gbar = hero.querySelector('.topo-hero-gatebar');
  ok(!!gbar, 'per-gate bar renders when >1 gate carries the metric');
  const gbtn = hero.querySelector('[data-hero-gate="cz_gaussian_bipolar"]');
  ok(!!gbtn, 'the gate variant is offered by name');
  gbtn.click();
  const evals2 = hero.querySelectorAll('.topo-hero-eval');
  ok(evals2[0] && /90\.2/.test(evals2[0].textContent),
     'picking a gate shows THAT gate variant RB (' + (evals2[0] && evals2[0].textContent) + ')');
  ok(win.localStorage.getItem('quam_topo_hero_gate') === 'cz_gaussian_bipolar',
     'the gate choice persists');
  hero.querySelector('[data-hero-gate="best"]').click();
  ok(hero.querySelectorAll('.topo-hero-node-neutral').length === 3,
     'edge mode: stones go neutral (edges carry the numbers)');
  ok(hero.querySelectorAll('.topo-hero-val').length === 0, 'edge mode: no stale node values');
  ok(win.localStorage.getItem('quam_topo_hero_metric_v2') === 'rb2q_standard', 'edge metric persists');
  const legend = hero.querySelector('.topo-hero-legend');
  ok(legend && !!legend.querySelector('.topo-hero-lg-grad'), 'edge mode: gradient legend renders');

  const hit = hero.querySelector('.topo-hero-edge-hit');
  ok(hit && hit.getAttribute('stroke-width') === '22',
     'edges carry a fat (22) hit area — the customer could not click the old 11');

  const eg = hero.querySelector('[data-hero-pair="q1-2"]');
  eg.dispatchEvent(new win.Event('mouseenter'));
  // v1.5.2: hover -> details opened on hover; now hover offers Expand and the press opens them
  { const xb = doc.querySelector('.topo-expand'); if (xb) xb.click(); }
  setTimeout(function () {
    const pop = doc.querySelector('.topo-pair-popup');
    ok(!!pop, 'pair hover opens the pair popup');
    ok(pop && /q1-2/.test(pop.textContent), 'pair popup names the pair');
    ok(pop && /95\.10%/.test(pop.textContent), 'pair popup lists the per-gate RB fidelity');
    ok(pop && /detuning/.test(pop.textContent), 'pair popup lists the parameters section');
    ok(pop && /detuning\s*-0\.1659 V/.test(pop.textContent), 'pair popup uses the declared volt unit');
    heroPrefersTheGateNumber();
    overviewTilesStateBothErrorRates();
    anharmSubLine();
    edgeDeltaLabels();
    edgeLabelsBesideTheBar();
    calibrationAgeNamesItsBasis();
    oneSourcePerPatch();
    overviewTileOneSource();
    finish();
  }, 400);
}

// ── docs/138: the hero must open on the GATE number, not the Clifford one ──
// Reported from the real chip: the map's top said 97.1% while the per-pulse
// sections underneath said 99.09% and 99.34% for the same pair. Same
// measurement campaign, two different quantities, and the smaller one on top.
function heroPrefersTheGateNumber() {
  const win = makeWorld();
  // A pair measured the way the lab measures: three CZ pulses, each with a
  // Standard (per-Clifford) and an Interleaved (per-gate) RB number. Values
  // are the real ones from #3424's q19-20.
  const topo = {
    nodes: [
      { id: 'q19', grid_location: '0,0', f_01: 4.8e9 },
      { id: 'q20', grid_location: '1,0', f_01: 5.1e9 },
    ],
    edges: [
      { pair_id: 'q19-20', source: 'q19', target: 'q20', has_cz: true,
        gate_kind: 'cz', directed: false, active: null,
        // what query.py now derives: the edge carries the GATE number and
        // names the pulse it came from
        cz_fidelity: 0.9933625133477547, fidelity_source: 'interleaved_rb',
        best_gate: 'cz_flattop',
        gate_fidelities: [
          { gate: 'cz_flattop', metric: 'StandardRB', value: 0.9706080538741072, level: 'clifford' },
          { gate: 'cz_flattop', metric: 'InterleavedRB', value: 0.9933625133477547, level: 'gate' },
          { gate: 'cz_gaussian_bipolar', metric: 'StandardRB', value: 0.9704607418362303, level: 'clifford' },
          { gate: 'cz_gaussian_bipolar', metric: 'InterleavedRB', value: 0.9909351938233361, level: 'gate' },
          { gate: 'cz_unipolar', metric: 'StandardRB', value: 0.9671621994719876, level: 'clifford' },
          { gate: 'cz_unipolar', metric: 'InterleavedRB', value: 0.9856971280318174, level: 'gate' },
          { gate: 'cz_flattop', metric: 'StandardRB_alpha', value: 0.960811, level: 'decay' },
        ] },
    ],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');

  const irb = hero.querySelector('[data-hero-metric="rb2q_interleaved"]');
  const srb = hero.querySelector('[data-hero-metric="rb2q_standard"]');
  ok(!!irb && !!srb, 'RB: both the gate and the Clifford patch are offered');
  ok(/IRB|gate/.test(irb.textContent), 'RB: the gate patch names itself');
  ok(/Clifford/.test(srb.textContent), 'RB: the Clifford patch names itself');

  // ORDER is the default: the gate metric must come before the Clifford one,
  // so a chip with no Bell_State opens on 99.3, not 97.1.
  const patches = Array.prototype.map.call(
    hero.querySelectorAll('[data-hero-metric]'),
    function (b) { return b.getAttribute('data-hero-metric'); });
  ok(patches.indexOf('rb2q_interleaved') < patches.indexOf('rb2q_standard'),
     'RB: the gate number is offered BEFORE the Clifford number (' + patches.join(',') + ')');

  irb.click();
  let ev = hero.querySelectorAll('.topo-hero-eval');
  ok(ev[0] && /99\.3/.test(ev[0].textContent),
     'RB: best shows the best INTERLEAVED value (' + (ev[0] && ev[0].textContent) + ')');

  // Clicking a pulse shows THAT pulse's interleaved number — the value the
  // per-pulse section further down the page prints.
  const gb = hero.querySelector('[data-hero-gate="cz_gaussian_bipolar"]');
  ok(!!gb, 'RB: the pulse bar offers each CZ variant');
  gb.click();
  ev = hero.querySelectorAll('.topo-hero-eval');
  ok(ev[0] && /99\.1/.test(ev[0].textContent),
     'RB: picking gaussian_bipolar shows ITS interleaved value, matching the '
     + 'section below (' + (ev[0] && ev[0].textContent) + ')');

  // And the Clifford number is still reachable, and is a DIFFERENT number.
  hero.querySelector('[data-hero-metric="rb2q_standard"]').click();
  ev = hero.querySelectorAll('.topo-hero-eval');
  ok(ev[0] && /97\.0/.test(ev[0].textContent),
     'RB: the Clifford patch still shows the Clifford value ('
     + (ev[0] && ev[0].textContent) + ')');

  // A stale pre-docs/138 choice must not pin the old default.
  ok(win.localStorage.getItem('quam_topo_hero_metric') === null,
     'RB: the old un-versioned metric key is not written any more');
}


// -- docs/139 follow-up (user-directed): the Overview's two RB tiles each
// state BOTH error rates - EPC (per Clifford) and EPG (per gate) - bridged
// only by the run's own average_gates_per_clifford; and the T2-echo +
// Coverage tiles are gone.
function overviewTilesStateBothErrorRates() {
  const win = makeWorld();
  win.document.body.insertAdjacentHTML('beforeend',
    '<div id="topo-overview-tiles"></div>');
  const topo = {
    nodes: [
      { id: 'q19', grid_location: '0,0', f_01: 4.8e9, gate_fidelity_avg: 0.999 },
      { id: 'q20', grid_location: '1,0', f_01: 5.1e9 },
    ],
    edges: [
      { pair_id: 'q19-20', source: 'q19', target: 'q20', has_cz: true,
        gate_kind: 'cz', directed: false, active: null,
        cz_fidelity: 0.9933625133477547, fidelity_source: 'interleaved_rb',
        best_gate: 'cz_flattop',
        cz_fidelity_updated_at: Date.now() - 36e5,
        gate_details: [
          { name: 'cz_unipolar', length: 60, amplitude: 0.1 },
          { name: 'cz_flattop', length: 84, amplitude: 0.1 },
        ],
        gate_fidelities: [
          { gate: 'cz_flattop', metric: 'StandardRB',
            value: 0.9706080538741072, level: 'clifford',
            derived_gate_fidelity: 0.9944, average_gates_per_clifford: 5.371 },
          { gate: 'cz_flattop', metric: 'InterleavedRB',
            value: 0.9933625133477547, level: 'gate' },
        ] },
    ],
  };
  mount(win, topo, []);
  const html = win.document.getElementById('topo-overview-tiles').innerHTML;
  ok(/EPC/.test(html), 'tiles: EPC stated');
  ok(/EPG/.test(html), 'tiles: EPG stated');
  ok(html.indexOf('2Q gate fid. (SRB') >= 0,
     'tiles: the SRB-derived GATE tile exists');
  ok(html.indexOf('2Q Clifford fid. (IRB') >= 0,
     'tiles: the IRB-derived CLIFFORD tile exists');
  ok(/99\.44/.test(html), 'tiles: the derived gate value renders');
  ok(/÷5\.37/.test(html), 'tiles: the SRB->gate divisor is named');
  ok(/×5\.37/.test(html), 'tiles: the IRB->Clifford multiplier is named');
  ok(!/T2 echo/i.test(html), 'tiles: T2 echo tile removed');
  ok(html.indexOf('CZ Coverage') < 0 && html.indexOf('pairs with CZ gate') < 0,
     'tiles: the old CZ Coverage tile removed');
  // docs/139 follow-up r2: the four context tiles + the 1Q EPG line.
  ok(html.indexOf('RB Coverage') >= 0 && /1\/1/.test(html),
     'tiles: RB coverage counts the measured pairs');
  ok(html.indexOf('2Q Gate Length') >= 0 && /84 ns/.test(html),
     'tiles: the best gate length renders in ns');
  ok(html.indexOf('Qubits In Spec') >= 0,
     'tiles: the in-spec tile exists');
  ok(/EPG 0\.10%/.test(html),
     'tiles: the 1Q tile states its EPG');
  ok(html.indexOf('Calibration Age') >= 0 && /today|yesterday|ago/.test(html),
     'tiles: the freshness tile renders an age');
}

/* ── customer ask (2026-08-27): anharmonicity rides under f01 ──────────────
 * The Qubit-freq patch used to print f01 alone; the anharmonicity matters as
 * much when scanning a chip, so it joins each stone as a SMALLER MHz sub-line.
 * Honesty pins: read through the same physical gate as every displayed number
 * (missing → absent, bad fit → absent, never a dash), no other metric grows a
 * sub-line, and the geometry stays uniform per metric so ids line up. */
function anharmSubLine() {
  const win = makeWorld();
  const topo = {
    nodes: [
      { id: 'q1', grid_location: '0,0', T1: 1e-5, f_01: 4.8e9, anharmonicity: -2.01e8 },
      { id: 'q2', grid_location: '1,0', T1: 2e-5, f_01: 5.1e9 },   // no anharm at all
      // bad fit: raw present, gated value null, not unresolved
      { id: 'q3', grid_location: '0,1', T1: 3e-5, f_01: 4.9e9,
        metrics: { anharmonicity: { value: null, raw: -4.7e8, unresolved: false } } },
    ],
    edges: [],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');
  ok(hero.querySelector('[data-hero-metric="f_01"]').className.indexOf('active') !== -1,
     'anharm: fresh world opens on the Qubit-freq patch');
  const sub1 = hero.querySelector('[data-hero-qubit="q1"] .topo-hero-sub');
  ok(!!sub1, 'anharm: a sub-line joins f01 inside the stone');
  ok(sub1 && /α -201\.0 MHz/.test(sub1.textContent),
     'anharm: rendered in MHz with the α label (' + (sub1 && sub1.textContent) + ')');
  ok(!hero.querySelector('[data-hero-qubit="q2"] .topo-hero-sub'),
     'anharm: a qubit without the value gets NO sub-line (never a dash)');
  ok(!hero.querySelector('[data-hero-qubit="q3"] .topo-hero-sub'),
     'anharm: an unphysical (bad-fit) value renders nothing');
  // uniform per-metric geometry: the sub-less q2's id sits at the SAME shifted
  // y as q1's, so the map's ids stay on one line.
  const idY = (q) => hero.querySelector('[data-hero-qubit="' + q + '"] .topo-hero-id')
      .getAttribute('y');
  ok(idY('q1') === idY('q2') && idY('q1') !== '-5',
     'anharm: three-line geometry is uniform across the map (id y=' + idY('q1') + ')');
  // another metric must NOT inherit the sub-line, and returns to the classic layout
  hero.querySelector('[data-hero-metric="T1"]').click();
  ok(hero.querySelectorAll('.topo-hero-sub').length === 0,
     'anharm: switching to T1 drops every sub-line');
  ok(idY('q1') === '-5', 'anharm: T1 keeps the classic two-line layout');
  // CSS source pins: the sub-line is genuinely SMALLER than the value line, in
  // both the normal and the compact font scales.
  const css = fs.readFileSync(
      path.join(ROOT, 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');
  const px = (re) => parseFloat((css.match(re) || [])[1]);
  const subPx = px(/^\.topo-hero-sub \{ font-size: ([\d.]+)px/m);
  const valPx = px(/^\.topo-hero-val \{ font-size: ([\d.]+)px/m);
  ok(subPx > 0 && valPx > 0 && subPx < valPx,
     'anharm: CSS keeps the sub-line smaller than the value (' + subPx + ' < ' + valPx + ')');
  const cSub = px(/^\.topo-hero-svg\.hero-compact \.topo-hero-sub \{ font-size: ([\d.]+)px/m);
  const cVal = px(/^\.topo-hero-svg\.hero-compact \.topo-hero-val \{ font-size: ([\d.]+)px/m);
  ok(cSub > 0 && cVal > 0 && cSub < cVal,
     'anharm: compact mode keeps the sub-line smaller too (' + cSub + ' < ' + cVal + ')');
}

/* ── customer ask (2026-08-27, round 2): Δf on every pair edge ─────────────
 * In the Qubit-freq view each edge prints the difference between its two
 * stones' f01 values ("Δf 300.0 MHz") on the line's midpoint. Honesty pins:
 * same gated read as the stones (either end missing ⇒ no label, never a
 * fabricated 0), deduped per physical pair on a CR chip's two directed
 * edges, absent on every other metric, and smaller than the edge-metric
 * value text. */
function edgeDeltaLabels() {
  const win = makeWorld();
  const topo = {
    nodes: [
      { id: 'q1', grid_location: '0,0', T1: 1e-5, f_01: 4.8e9 },
      { id: 'q2', grid_location: '1,0', T1: 2e-5, f_01: 5.1e9 },
      { id: 'q3', grid_location: '0,1', T1: 3e-5, f_01: 4.9e9 },
      { id: 'q4', grid_location: '1,1', T1: 4e-5 },              // no f_01
    ],
    edges: [
      { pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.97,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz' },
      // a CR pair: BOTH directions ship as separate edges — ONE label only
      { pair_id: 'q1-3', source: 'q1', target: 'q3', has_cz: true, cz_fidelity: null,
        gate_kind: 'cr', directed: true, active: null, best_gate: null },
      { pair_id: 'q3-1', source: 'q3', target: 'q1', has_cz: true, cz_fidelity: null,
        gate_kind: 'cr', directed: true, active: null, best_gate: null },
      { pair_id: 'q2-4', source: 'q2', target: 'q4', has_cz: false, cz_fidelity: null,
        gate_kind: 'none', directed: false, active: null, best_gate: null },
    ],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');
  const texts = () => Array.prototype.map.call(
      hero.querySelectorAll('.topo-hero-edelta'), (t) => t.textContent);
  // v1.3.0: three lines -> two; keep the number and unit together.
  // Horizontal edges stack the metric and value; vertical edges keep one line.
  const stacked = hero.querySelector('.topo-hero-edelta-stack');
  ok(!!stacked && Array.prototype.map.call(stacked.querySelectorAll('tspan'), (t) => t.textContent)
        .join('|') === 'Δf|300 MHz',
     'edelta: a horizontal edge stacks the metric and 300 MHz on two lines ('
     + (stacked && stacked.textContent) + ')');
  ok(texts().filter((t) => t === 'Δf 100 MHz').length === 1,
     'edelta: a CR pair\'s two directed edges dedupe to ONE label, one line on a vertical edge');
  ok(texts().length === 2,
     'edelta: an edge whose endpoint has no f01 gets NO label — never a '
     + 'fabricated 0 (got ' + texts().length + ')');
  hero.querySelector('[data-hero-metric="T1"]').click();
  ok(hero.querySelectorAll('.topo-hero-edelta').length === 0,
     'edelta: only the Qubit-freq view carries Δf labels');
  hero.querySelector('[data-hero-metric="bell_state"]').click();
  ok(hero.querySelectorAll('.topo-hero-edelta').length === 0,
     'edelta: edge-metric mode keeps the edges for its OWN numbers');
  // CSS: smaller + haloed like the edge-metric value, in both font scales
  const css = fs.readFileSync(
      path.join(ROOT, 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');
  const px = (re) => parseFloat((css.match(re) || [])[1]);
  const dPx = px(/^\.topo-hero-edelta \{\n?\s*font-size: ([\d.]+)px/m);
  const ePx = px(/^\.topo-hero-eval \{\n?\s*font-size: ([\d.]+)px/m);
  ok(dPx > 0 && ePx > 0 && dPx < ePx,
     'edelta: CSS keeps Δf smaller than the edge-metric value (' + dPx + ' < ' + ePx + ')');
  // Round 3 (customer: "too big — anharmonicity size is enough"): the Δf label
  // is exactly the stone sub-line's size, in both font scales.
  const sPx = px(/^\.topo-hero-sub \{ font-size: ([\d.]+)px/m);
  ok(dPx === sPx, 'edelta: Δf is anharmonicity-sized (' + dPx + ' == ' + sPx + ')');
  const cD = px(/^\.topo-hero-svg\.hero-compact \.topo-hero-edelta \{ font-size: ([\d.]+)px/m);
  const cE = px(/^\.topo-hero-svg\.hero-compact \.topo-hero-eval \{ font-size: ([\d.]+)px/m);
  const cS = px(/^\.topo-hero-svg\.hero-compact \.topo-hero-sub \{ font-size: ([\d.]+)px/m);
  ok(cD > 0 && cE > 0 && cD < cE && cD === cS,
     'edelta: compact mode keeps it anharmonicity-sized too (' + cD + ' == ' + cS + ' < ' + cE + ')');
  ok(/\.topo-hero-edelta \{[^}]*paint-order: stroke/.test(css),
     'edelta: halo (paint-order stroke) so it reads over the edge line');
}

// ── QA F-26: a printed edge value sits BESIDE its bar, never on it ──────────
// On the real 5Q chip the Δf number sat exactly on the edge line ('1.49' read
// as '1:49'), the chevron touched 'Δf', and the on-line C/T circles crowded the
// IRB percentage ('C95:7%T'). Every label is now placed off the centre line on
// the side the chevron leaves free, outside the bar band, and clear of an M
// stacked on that side. Measured here from the DOM the hero actually emits,
// against the chevron / marker geometry the SHARED TopoGraph.pairGlyphs draws
// (pinned byte-identical elsewhere and untouched by the fix). A vertical
// pair's chevron side depends on the layout's row direction, so it is READ
// from the drawn chevron, never assumed.
function edgeLabelsBesideTheBar() {
  const win = makeWorld();
  const topo = {
    nodes: [
      { id: 'q1', grid_location: '0,0', T1: 1e-5, f_01: 4.8e9 },
      { id: 'q2', grid_location: '1,0', T1: 2e-5, f_01: 5.1e9 },
      { id: 'q3', grid_location: '0,1', T1: 3e-5, f_01: 4.9e9 },
      { id: 'q4', grid_location: '1,1', T1: 4e-5, f_01: 4.95e9 },
    ],
    edges: [
      // horizontal, undirected, no role: hi = q2 (right) → chevron BELOW
      { pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.97,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz' },
      // a vertical CR pair, both directions (two lines ±6 off the column)
      { pair_id: 'q1-3', source: 'q1', target: 'q3', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.93,
        gate_kind: 'cr', directed: true, active: null, best_gate: 'cr' },
      { pair_id: 'q3-1', source: 'q3', target: 'q1', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.91,
        gate_kind: 'cr', directed: true, active: null, best_gate: 'cr' },
      // vertical, undirected, the higher f_01 at the OTHER end → chevron on the other side
      { pair_id: 'q2-4', source: 'q2', target: 'q4', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.90,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz', moving_qubit: 'control' },
      // horizontal with a MOVING qubit: hi = q4 → chevron BELOW, M ABOVE — the
      // value goes above too and must clear the M circle, not just the bar
      { pair_id: 'q3-4', source: 'q3', target: 'q4', has_cz: true, fidelity_source: 'macro', cz_fidelity: 0.88,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz', moving_qubit: 'target' },
    ],
  };
  mount(win, topo, []);
  const hero = win.document.getElementById('topo-hero');
  const num = (el, a) => parseFloat(el && el.getAttribute(a));
  const fin = (v) => typeof v === 'number' && isFinite(v);
  const hasCls = (el, c) => !!el && (' ' + (el.getAttribute('class') || '') + ' ').indexOf(' ' + c + ' ') >= 0;
  // a pair's centre line, from the line(s) the hero draws: an undirected pair's
  // one line IS the centre; a CR pair's two lines sit ±6 either side of it
  const lineOf = (pairId) => hero.querySelector('.topo-hero-edge[data-hero-pair="' + pairId + '"] line');
  const lineMid = (pairId) => {
    const l = lineOf(pairId);
    return { x: (num(l, 'x1') + num(l, 'x2')) / 2, y: (num(l, 'y1') + num(l, 'y2')) / 2 };
  };
  const mid = (pa, pb) => pb ? { x: (lineMid(pa).x + lineMid(pb).x) / 2, y: (lineMid(pa).y + lineMid(pb).y) / 2 }
                             : lineMid(pa);
  // half the drawn width of a pair's first line
  const barHalf = (pairId) => num(lineOf(pairId), 'stroke-width') / 2;
  // D7: same offset for level, steep, reversed and moving-role edges.
  function checkLabels() {
    const texts = Array.from(hero.querySelectorAll('.topo-hero-edelta'));
    const h = texts.filter(t=>t.classList.contains('topo-hero-edelta-stack'));
    const v = texts.filter(t=>!t.classList.contains('topo-hero-edelta-stack'));
    const off = mid('q1-2').y - num(h[0], 'y') - 9;
    const push = (t) => (t.getAttribute('data-link-push') || '0 0').split(' ').map(Number);
    ok(h.length === 2 && v.length === 2, 'D7: level and steep delta fixtures render');
    ok(off > 0 && off <= 12, 'D10: at rest a level label sits just above its edge (' + off + ')');
    ok(Math.abs(mid('q3-4').y - num(h[1], 'y') - 9 - off) < 0.01, 'D10: every level label has the same rest offset despite moving role');
    v.forEach(t => {
      const mx = t.textContent.includes('100') ? mid('q1-3','q3-1').x : mid('q2-4').x;
      ok(Math.abs(mx - num(t,'x') - off) < 0.01 && hasCls(t,'topo-hero-lbl-end'), 'D10: every steep label same small left offset at rest');
      const vbX = +hero.querySelector('svg.topo-hero-svg').getAttribute('viewBox').split(' ')[0];
      ok(num(t,'x') + push(t)[0] - t.textContent.length * 8 * 0.7 >= vbX, 'D12: the viewBox reaches every steep label, pushed too (no first-column cut)');
      ok(push(t)[1] === 0 && push(t)[0] < 0, 'D11: a steep label is pushed left only');
    });
    h.forEach(t => ok(push(t)[0] === 0 && push(t)[1] < 0, 'D11: a level label is pushed up only'));
    const lit = off + Math.abs(push(h[0])[1]);
    texts.forEach(t => ok(Math.abs(Math.hypot(...push(t)) + off - lit) < 0.01, 'D11: one hovered offset for every label'));
    hero.querySelectorAll('.topo-pair-badge rect').forEach(r=>{
      ok(lit > Math.hypot(num(r,'width'),num(r,'height'))/2 + 5, 'D11: a hovered label clears the full expanded patch even when dense');
    });
    return off;
  }
  checkLabels();
  ok(!hero.querySelector('.cm-freq, .cm-role circle, .cm-dir-m'), 'D1/D5: no frequency arrow or old moving dots');
  ok(hero.querySelectorAll('svg .cm-role text').length === topo.edges.length * 2 && Array.from(hero.querySelectorAll('svg .cm-role text')).every(t=>/[HL]$/.test(t.textContent)), 'D3: H/L present in hero Qubit freq view');
  ok(/C control.*T target.*M moving qubit.*H higher f01.*L lower f01/.test(hero.querySelector('.topo-hero-legend').textContent), 'D6: hero has shared complete legend');
  hero.querySelectorAll('svg .cm-role').forEach(pill => {
    const text = pill.querySelector('text'), rect = pill.querySelector('rect');
    ok(num(text, 'font-size') >= 7 && num(text, 'font-size') <= 8 && num(rect, 'height') <= 13, 'D4: hero pill size capped despite 132px cells');
    const node = hero.querySelector('[data-hero-qubit="'+pill.getAttribute('data-cm-at')+'"]');
    const [cx,cy] = node.getAttribute('transform').match(/[-\d.]+/g).map(Number);
    const nearest = (x,y) => Math.hypot(x-Math.max(num(rect,'x'),Math.min(x,num(rect,'x')+num(rect,'width'))), y-Math.max(num(rect,'y'),Math.min(y,num(rect,'y')+num(rect,'height'))));
    ok(nearest(cx,cy) > num(node.querySelector('circle'),'r'), 'D2/D4: hero pill clears actual stone radius');
    const badge = hero.querySelector('.topo-pair-badge[data-hero-pair="'+pill.getAttribute('data-cm-pair')+'"]');
    ok(!badge.querySelector('circle'), 'D9: no retained midpoint marker beside hero pill');
  });
  // Dense and expanded patches reserve identical room. Reverse frequencies,
  // make a tiny difference, and force the dense path with isolated nodes.
  topo.nodes[1].f_01 = topo.nodes[0].f_01 + 1;
  for (let i=0;i<12;i++) topo.nodes.push({id:'extra'+i,grid_location:(i+2)+',2'});
  mount(win, topo, []);
  ok(!!hero.querySelector('.topo-pair-badge-dense'), 'D7: dense hover fixture');
  checkLabels();
  const badge = hero.querySelector('.topo-pair-badge');
  const before = Array.from(hero.querySelectorAll('.topo-hero-edelta')).map(t=>t.outerHTML).join('');
  badge.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const ends = badge.getAttribute('data-link-ends');
  const labs = Array.from(hero.querySelectorAll('.topo-hero-edelta'));
  const mine = labs.filter(t => t.getAttribute('data-link-ends') === ends);
  ok(mine.length === 1 && mine[0].getAttribute('transform') === 'translate(' + mine[0].getAttribute('data-link-push').split(' ').join(',') + ')',
     'D11: hovering a pair pushes its own delta label out');
  ok(labs.filter(t => t !== mine[0]).every(t => !t.hasAttribute('transform')), 'D11: labels of other pairs do not move');
  ok(mine[0].style.visibility !== 'hidden', 'D11: the pushed label stays visible');
  hero.dispatchEvent(new win.MouseEvent('mouseleave'));
  ok(labs.every(t => !t.hasAttribute('transform')), 'D11: leaving the map puts every label back at rest');
  ok(Array.from(hero.querySelectorAll('.topo-hero-edelta')).map(t=>t.outerHTML).join('') === before, 'D11: rest markup identical after hover ends');
  hero.querySelector('[data-hero-metric="bell_state"]').click();
  const e12 = Array.from(hero.querySelectorAll('.topo-hero-eval')).find(t=>/^97\.0/.test(t.textContent));
  ok(e12 && num(e12,'y') < mid('q1-2').y - barHalf('q1-2'), 'edge metric remains above bar');
  // the side anchors are stylesheet rules: `.topo-hero-eval { text-anchor: middle }`
  // beats a presentation attribute (the trap CLAUDE.md records three times)
  const css = read('style.css');
  ok(/\.topo-hero-svg \.topo-hero-lbl-start \{ text-anchor: start; \}/.test(css)
     && /\.topo-hero-svg \.topo-hero-lbl-end \{ text-anchor: end; \}/.test(css),
     'F-26: the start/end anchors exist as class rules in style.css');
}

/* docs/301 F11: an entity's last_calibrated may come from the change ledger
   (a run's saved state moved one of its values) instead of a *_updated_at
   stamp. The tile says how many of each; the map's age cell says which. */
function calibrationAgeNamesItsBasis() {
  const win = makeWorld();
  win.document.body.insertAdjacentHTML('beforeend', '<div id="topo-overview-tiles"></div>');
  const now = Date.now();
  const topo = {
    nodes: [
      { id: 'q1', grid_location: '0,0', T1: 1e-5, last_calibrated: now - 864e5, last_calibrated_from: 'run' },
      { id: 'q2', grid_location: '1,0', T1: 2e-5, last_calibrated: now - 8 * 864e5, last_calibrated_from: 'stamp' },
    ],
    edges: [
      { pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
        gate_kind: 'cz', directed: false, active: null, best_gate: 'cz',
        cz_fidelity_updated_at: now - 30 * 864e5,
        last_calibrated: now - 2 * 864e5, last_calibrated_from: 'run' },
    ],
  };
  mount(win, topo, []);
  const html = win.document.getElementById('topo-overview-tiles').textContent;
  ok(/Calibration Age/.test(html) && /oldest 8 days ago/.test(html),
     'F11: the oldest entity is the stamped one (the edge ages by its run change, not its 30-day CZ stamp): '
     + (html.match(/Calibration Age.{0,80}/) || [''])[0]);
  ok(/2 moved in a run, 1 stamped/.test(html),
     'F11: the tile says how many ages come from a run change and how many from a stamp');
  const hero = win.document.getElementById('topo-hero');
  const btn = hero.querySelector('[data-hero-metric="last_calibrated"]');
  ok(!!btn, 'F11 fixture: the Last calibrated map metric exists');
  if (btn) btn.click();
  const q1 = hero.querySelector('[data-hero-qubit="q1"]'), q2 = hero.querySelector('[data-hero-qubit="q2"]');
  ok(q1 && /last moved in a run’s saved state/.test(q1.innerHTML) && /change ledger/.test(q1.innerHTML),
     'F11: a run-dated stone says so on hover');
  ok(q2 && /last calibrated/.test(q2.innerHTML) && !/moved in a run/.test(q2.innerHTML),
     'F11: a stamp-dated stone keeps its own words');
}

// User decision 2026-11-08: every 2Q patch reads exactly one measurement.
// On a chip whose edge number comes from interleaved RB, the old "2Q gate fid."
// patch duplicated "2Q gate (IRB)". It is gone; Bell state fid. stays listed
// and says where it fills from; a remembered cz_fidelity lands on the default.
function oneSourcePerPatch() {
  function irbTopo() {
    return { nodes: [{ id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '1,0' }],
      edges: [{ pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, gate_kind: 'cz',
        cz_fidelity: 0.991, fidelity_source: 'interleaved_rb', best_gate: 'cz',
        gate_fidelities: [{ gate: 'cz', metric: 'InterleavedRB', value: 0.991, level: 'gate' },
                          { gate: 'cz', metric: 'StandardRB', value: 0.95, level: 'clifford' }] }] };
  }
  let win = makeWorld();
  win.localStorage.setItem('quam_topo_hero_metric_v2', 'cz_fidelity');
  mount(win, irbTopo(), []);
  let hero = win.document.getElementById('topo-hero');
  ok(!hero.querySelector('[data-hero-metric="cz_fidelity"]'), 'one source: no cz_fidelity patch on an IRB chip');
  const keys = Array.prototype.map.call(hero.querySelectorAll('[data-hero-metric]'),
    function (b) { return b.getAttribute('data-hero-metric'); });
  ok(keys.indexOf('rb2q_interleaved') >= 0 && keys.indexOf('bell_state') >= 0,
     'one source: IRB and Bell patches listed (' + keys.join(',') + ')');
  const act = hero.querySelector('.topo-hero-mbtn.active');
  ok(act && act.getAttribute('data-hero-metric') === 'rb2q_interleaved',
     'one source: a remembered cz_fidelity opens on the IRB patch (' + (act && act.getAttribute('data-hero-metric')) + ')');
  ['bell_state', 'rb2q_interleaved', 'rb2q_standard'].forEach(function (k) {
    const b = hero.querySelector('[data-hero-metric="' + k + '"]');
    ok(b && (b.getAttribute('title') || '').length > 20, 'one source: ' + k + ' says what it measures');
  });
  hero.querySelector('[data-hero-metric="bell_state"]').click();
  ok(hero.querySelectorAll('.topo-hero-eval').length === 0, 'one source: Bell shows no value from IRB data');
  ok(/Bell_State/.test(hero.querySelector('.topo-hero-legend').textContent),
     'one source: an empty Bell patch names its source leaf');

  // A Bell-measured pair: the Bell patch shows the Bell number.
  win = makeWorld();
  const t = irbTopo();
  t.edges[0].fidelity_source = 'macro'; t.edges[0].cz_fidelity = 0.973;
  mount(win, t, []);
  hero = win.document.getElementById('topo-hero');
  hero.querySelector('[data-hero-metric="bell_state"]').click();
  const ev = hero.querySelectorAll('.topo-hero-eval');
  ok(ev[0] && /97\.3/.test(ev[0].textContent), 'one source: the Bell patch prints the Bell value');
}

// The Overview 2Q tile shows the SAME default source as the map (one shared
// chooser), titled by it; a pair without that source is '—' in the hover
// list, never filled from cz_fidelity's per-pair fallback.
function overviewTileOneSource() {
  const win = makeWorld();
  win.document.body.insertAdjacentHTML('beforeend', '<div id="topo-overview-tiles"></div>');
  const topo = {
    nodes: [{ id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '1,0' },
            { id: 'q3', grid_location: '2,0' }],
    edges: [
      // Bell-only pair: cz_fidelity is its Bell value
      { pair_id: 'q1-2', source: 'q1', target: 'q2', has_cz: true, gate_kind: 'cz',
        cz_fidelity: 0.961, fidelity_source: 'macro', best_gate: 'cz', gate_fidelities: [] },
      // IRB-only pair
      { pair_id: 'q2-3', source: 'q2', target: 'q3', has_cz: true, gate_kind: 'cz',
        cz_fidelity: 0.992, fidelity_source: 'interleaved_rb', best_gate: 'cz',
        gate_fidelities: [{ gate: 'cz', metric: 'InterleavedRB', value: 0.992, level: 'gate' }] },
    ],
  };
  mount(win, topo, []);
  const host = win.document.getElementById('topo-overview-tiles');
  const tile = host.querySelector('.topo-card[data-tile-id="gate2q"]');
  ok(!!tile, 'ov 2Q: the gate2q tile exists');
  ok(tile && /2Q gate \(IRB\)/.test(tile.textContent) && !/\(Best\)/.test(tile.textContent),
     'ov 2Q: the tile is titled by its one source (' + (tile && tile.textContent.slice(0, 60)) + ')');
  ok(tile && /99\.2/.test(tile.textContent) && !/96\.1|97\.6/.test(tile.textContent),
     'ov 2Q: the tile number uses only the IRB pair');
  tile.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const pop = win.document.getElementById('ov-hover-pop');
  ok(!!pop, 'ov 2Q: hover list opens');
  ok(pop && !/96\.1/.test(pop.textContent), 'ov 2Q: the Bell-only pair is not filled with its Bell value');
  ok(pop && /q1-2/.test(pop.textContent) && /—/.test(pop.textContent),
     'ov 2Q: the pair without the source reads —');
  // Edge colour follows the same one source: the Bell-only pair is not
  // painted from its Bell number on an IRB chip.
  win.document.querySelector('#topo-hero [data-hero-metric="diag"]').click();   // a node view: edges use _edgePaint
  const strokes = {};
  Array.prototype.forEach.call(win.document.querySelectorAll('#topo-hero .topo-hero-edge'), function (g) {
    strokes[g.getAttribute('data-hero-pair')] = g.querySelector('line').getAttribute('stroke');
  });
  ok(strokes['q1-2'] === '#bbbbbb' && strokes['q2-3'] && strokes['q2-3'] !== '#bbbbbb',
     'ov 2Q: edges are coloured by the one source (' + JSON.stringify(strokes) + ')');
  ok(!host.querySelector('.topo-card[data-tile-id="irb"]'),
     'ov 2Q: no duplicate IRB tile beside the IRB-sourced 2Q tile');

  // Prefs stored for the retired 'irb' tile carry over to gate2q.
  const w2 = makeWorld();
  w2.document.body.insertAdjacentHTML('beforeend', '<div id="topo-overview-tiles"></div>');
  w2.localStorage.setItem('quam_overview_tiles_v1',
    JSON.stringify({ order: ['irb', 'chip_size'], stats: { irb: 'max' } }));
  mount(w2, topo, []);
  const first = w2.document.querySelector('#topo-overview-tiles .topo-card[data-tile-id]');
  ok(first && first.getAttribute('data-tile-id') === 'gate2q',
     'ov 2Q: a stored irb position lands on gate2q (' + (first && first.getAttribute('data-tile-id')) + ')');
  const g2 = w2.document.querySelector('.topo-card[data-tile-id="gate2q"]');
  ok(g2 && /max/i.test(g2.textContent.split('med')[0]) ,
     'ov 2Q: a stored irb statistic lands on gate2q (' + (g2 && g2.textContent.slice(0, 50)) + ')');
}

// D9: dense heroes have no midpoint circles and keep both hover entry points.
{
  const win = makeWorld(), topo = {nodes:[], edges:[]};
  for (let r=0; r<4; r++) for (let c=0; c<4; c++) {
    topo.nodes.push({id:'q'+r+c, grid_location:c+','+r, f_01:5e9+c*1e8});
    if (c) topo.edges.push({pair_id:'q'+r+(c-1)+'-q'+r+c, source:'q'+r+(c-1), target:'q'+r+c});
  }
  win.localStorage.setItem('quam_topo_hero_metric_v2','f_01');
  const css=read('style.css'), style=win.document.createElement('style');
  style.textContent=css.slice(css.indexOf('.topo-pair-badge {'),css.indexOf('.topo-pair-badge-stale,'));
  win.document.head.appendChild(style);
  mount(win,topo,[]);
  const hero=win.document.getElementById('topo-hero');
  ok(!hero.querySelector('.topo-pair-badge-dot, svg circle:not(.topo-hero-stone)'),
     'D9: hero has no midpoint circle, including hidden legacy dots');
  const badge=hero.querySelector('.topo-pair-badge'), pill=badge.querySelector('.topo-pair-badge-pill');
  const hit=badge.querySelector('.topo-hero-edge-hit'), edge=hero.querySelector('.topo-hero-edge');
  ok(hit && hit.getAttribute('fill') === 'transparent' && hit.getAttribute('stroke') === 'none'
     && hit.getAttribute('pointer-events') === 'all' && +hit.getAttribute('width') > 16,
     'D9: badge has an invisible reachable hit area');
  ok(win.getComputedStyle(pill).display === 'none', 'D9: dense badge hidden before hover');
  function hover(target,owner) {
    target.dispatchEvent(new win.MouseEvent('mouseover',{bubbles:true}));
    owner.dispatchEvent(new win.MouseEvent('mouseenter'));
  }
  hover(edge.querySelector('.topo-hero-edge-hit'),edge);
  ok(badge.classList.contains('topo-linked') && win.getComputedStyle(pill).display === 'inline'
     && !!win.document.querySelector('.topo-expand'), 'D9: edge hover reveals dense badge and Expand');
  hero.dispatchEvent(new win.MouseEvent('mouseleave'));
  win.TopoGraph.closeDetails();
  if (hit) hover(hit,badge);
  ok(badge.classList.contains('topo-linked') && win.getComputedStyle(pill).display === 'inline'
     && !!win.document.querySelector('.topo-expand'),
     'D9: invisible badge hit area reveals badge and Expand');
}

function finish() {
  if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
  console.log('chip_status_hero_selfcheck: all checks passed');
}
