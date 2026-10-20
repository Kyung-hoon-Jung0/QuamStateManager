/* Pairs as first-class objects on the Chip Status hero map, against the REAL
 * app.js + topo-graph.js + chip-status.js (and the component-map drawing)
 * under jsdom.
 *
 * Pins: a midpoint badge with the pair name and the 2Q fidelity (name only
 * when nothing is measured); the badge colour is the SAME chip-relative scale
 * the 2Q metric paints edges with (and the neutral no-data colour without a
 * value); a ~16 px hit area; solid / dashed / dotted / faded by data; hover
 * on a pair offers only Expand and Expand opens the pair panel (operating
 * point, provenance only when proven, links); linked highlight both ways;
 * the density fallback on a large chip.
 *
 * Run: node tests/topology_pairs_selfcheck.cjs   (driven by tests/test_topology_pairs.py)
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

let fails = 0, checks = 0;
function ok(c, m) { checks++; if (!c) { console.error('FAIL: ' + m); fails++; } else console.log('ok - ' + m); }

function makeWorld(metric) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>'
    + '<div id="topo-hero"></div><div id="topo-html-wrap"></div><div id="table-pane"></div>'
    + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  if (metric) win.localStorage.setItem('quam_topo_hero_metric_v2', metric);
  win.htmx = { ajax: function () { win._htmxCalls.push(Array.prototype.slice.call(arguments)); },
               process: function () {} };
  win._htmxCalls = [];
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  return win;
}
function mount(win, topo) {
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {} });
}
function ev(win, el, type) {
  el.dispatchEvent(new win.MouseEvent(type, { bubbles: type !== 'mouseenter' && type !== 'mouseleave' }));
}
const DAY = 86400000;
function smallChip() {
  return {
    nodes: [
      { id: 'q1', grid_location: '0,0', f_01: 5.0e9 },
      { id: 'q2', grid_location: '1,0', f_01: 5.2e9 },
      { id: 'q3', grid_location: '0,1', f_01: 5.1e9 },
      { id: 'q4', grid_location: '1,1', f_01: 5.3e9 },
    ],
    edges: [
      { pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
        cz_fidelity_updated_at: Date.now() - 2 * DAY, cz_fidelity_run: 412,
        gate_kind: 'cz', directed: false, best_gate: 'cz',
        gate_details: [{ name: 'cz', amplitude: 0.1234, length: 48 }],
        last_calibrated: Date.now() - 2 * DAY },
      { pair_id: 'q3-q4', source: 'q3', target: 'q4', has_cz: true, cz_fidelity: 0.90,
        cz_fidelity_updated_at: Date.now() - 60 * DAY,
        gate_kind: 'cz', directed: false, best_gate: 'cz' },
      { pair_id: 'q1-q3', source: 'q1', target: 'q3', has_cz: true, cz_fidelity: null,
        gate_kind: 'cz', directed: false, best_gate: null },
      { pair_id: 'q2-q4', source: 'q2', target: 'q4', has_cz: false, cz_fidelity: null,
        gate_kind: 'none', directed: false, best_gate: null },
    ],
  };
}
function badge(win, id) { return win.document.querySelector('.topo-pair-badge[data-hero-pair="' + id + '"]'); }
function edge(win, id) { return win.document.querySelector('.topo-hero-edge[data-hero-pair="' + id + '"]'); }
function norm(c) {
  c = String(c || '').trim().toLowerCase();
  const m = /^#([0-9a-f]{6})$/.exec(c);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)).join(',');
  const r = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(c);
  return r ? [r[1], r[2], r[3]].join(',') : c;
}
function rectFill(b) { const r = b && b.querySelector('rect'); return r ? norm(r.style.fill) : null; }

(async function main() {
  // 1) badges: name + value / name only
  const win = makeWorld('f_01');
  mount(win, smallChip());
  const b12 = badge(win, 'q1-q2'), b13 = badge(win, 'q1-q3');
  ok(!!b12 && /q1-2/.test(b12.textContent) && /97\.0%/.test(b12.textContent),
     'a measured pair badge carries its name and the 2Q fidelity');
  ok(!!b13 && /q1-3/.test(b13.textContent) && !/%/.test(b13.textContent),
     'a pair with no measured value shows its name only');

  // 2) colour from the shared scale
  // app.js light-theme dashboard palette (UI_CONFIG is not a window global)
  const dCfg = { colorScale: ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'], nullCellColor: '#f0f0f0' };
  const stops = dCfg.colorScale;
  ok(rectFill(b12) === norm(stops[stops.length - 1]) && rectFill(badge(win, 'q3-q4')) === norm(stops[0]),
     'badge fills span the shared heatmap scale (best = last stop, worst = first)');
  ok(rectFill(b13) === norm(dCfg.nullCellColor), 'no value -> the neutral no-data colour');
  const winE = makeWorld('cz_fidelity');
  mount(winE, smallChip());
  const eLine = edge(winE, 'q1-q2').querySelector('.topo-hero-edge-line');
  ok(norm(eLine.getAttribute('stroke')) === rectFill(b12),
     'the badge colour equals the colour the 2Q edge metric paints that pair');

  // 3) hit area + 4) style by data
  const hit = edge(win, 'q1-q2').querySelector('.topo-hero-edge-hit');
  ok(hit && +hit.getAttribute('stroke-width') >= 16,
     'each pair edge has a wide (at least 16) invisible hit area');
  const line = (id) => edge(win, id).querySelector('.topo-hero-edge-line');
  ok(!line('q1-q2').getAttribute('stroke-dasharray'), 'a measured pair is drawn solid');
  ok(!!line('q1-q3').getAttribute('stroke-dasharray'), 'a defined but unmeasured 2Q gate is dashed');
  ok(line('q2-q4').getAttribute('stroke-dasharray') !== line('q1-q3').getAttribute('stroke-dasharray')
     && !!line('q2-q4').getAttribute('stroke-dasharray'), 'a pair with no 2Q gate is dotted, distinct from dashed');
  ok(edge(win, 'q3-q4').classList.contains('topo-hero-edge-stale') && badge(win, 'q3-q4').classList.contains('topo-pair-badge-stale')
     && !edge(win, 'q1-q2').classList.contains('topo-hero-edge-stale'),
     'a value older than the chip age rule is faded; a fresh one is not');
  ok(+line('q1-q2').getAttribute('stroke-width') >= 7, 'the default pair stroke is thicker');

  // 5) linked highlight
  ev(win, edge(win, 'q1-q2').querySelector('.topo-hero-edge-hit'), 'mouseover');
  const lit = (sel) => win.document.querySelector(sel).classList.contains('topo-linked');
  ok(lit('[data-hero-qubit="q1"]') && lit('[data-hero-qubit="q2"]') && !lit('[data-hero-qubit="q3"]'),
     'hovering a pair lights its two qubits only');
  ev(win, win.document.querySelector('[data-hero-qubit="q4"]'), 'mouseover');
  ok(edge(win, 'q3-q4').classList.contains('topo-linked') && edge(win, 'q2-q4').classList.contains('topo-linked')
     && !edge(win, 'q1-q2').classList.contains('topo-linked') && !lit('[data-hero-qubit="q1"]'),
     'hovering a qubit lights its pairs edges only');

  // 6) Expand on pair hover -> pair panel
  ev(win, edge(win, 'q1-q2'), 'mouseenter');
  const btn = win.document.querySelector('.topo-expand');
  ok(!!btn && !win.document.querySelector('.topo-pair-popup'), 'pair hover shows only the Expand button');
  if (btn) btn.click();
  const pop = win.document.querySelector('.topo-pair-popup');
  ok(!!pop && pop.getAttribute('role') === 'dialog', 'Expand opens the pair panel as a dialog');
  ok(pop && /Operating point/.test(pop.textContent) && /0\.1234 V/.test(pop.textContent),
     'the pair panel lists the CZ operating point');
  ok(pop && /set by run/.test(pop.textContent) && /#412/.test(pop.textContent),
     'the panel names the run that set the fidelity when that run is recorded');
  const plan = pop && pop.querySelector('a[href*="/cz-plan?pair="]');
  ok(!!plan && /q1-q2/.test(decodeURIComponent(plan.getAttribute('href'))), 'the panel links the pair CZ gate plan');
  const insp = pop && pop.querySelector('.topo-popup-inspect');
  ok(!!insp, 'the panel offers the pair inspector');
  if (insp) insp.click();
  ok(!win.document.querySelector('.topo-pair-popup') && win._htmxCalls.some((c) => /\/pair\/q1-q2/.test(c[1])),
     'Pair inspector opens the inspector and closes the panel');
  // provenance only when proven
  ev(win, badge(win, 'q3-q4'), 'mouseenter');
  const btn2 = win.document.querySelector('.topo-expand');
  ok(!!btn2, 'a badge hover offers Expand too');
  if (btn2) btn2.click();
  const pop2 = win.document.querySelector('.topo-pair-popup');
  ok(!!pop2 && !/set by run/.test(pop2.textContent), 'no recorded run -> no provenance row');
  win.document.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  ok(!win.document.querySelector('.topo-pair-popup'), 'Escape closes the pair panel');

  // 7) density: a large chip renders dots, the pill is still in the markup
  const big = { nodes: [], edges: [] };
  for (let r = 0; r < 5; r++) for (let c = 0; c < 4; c++) {
    big.nodes.push({ id: 'q' + (r * 4 + c + 1), grid_location: c + ',' + r, f_01: 5e9 + r * 1e7 });
  }
  big.nodes.push({ id: 'q21', grid_location: '4,0', f_01: 5e9 });
  for (let i = 1; i < 20; i++) if (i % 4) big.edges.push({ pair_id: 'q' + i + '-q' + (i + 1), source: 'q' + i,
    target: 'q' + (i + 1), has_cz: true, cz_fidelity: 0.9 + i / 1000, gate_kind: 'cz' });
  const winB = makeWorld('f_01');
  mount(winB, big);
  const bigBadges = winB.document.querySelectorAll('.topo-pair-badge');
  ok(bigBadges.length === big.edges.length && Array.prototype.every.call(bigBadges,
     (b) => b.classList.contains('topo-pair-badge-dense') && b.querySelector('.topo-pair-badge-pill')),
     'a 21-qubit chip draws every badge as a dot, keeping the pill for hover');
  ok(!badge(win, 'q1-q2').classList.contains('topo-pair-badge-dense'), 'a small chip keeps the full pill');

  // 8) component maps: same data-driven edge style + linked markup
  const host = win.document.createElement('div');
  win.document.body.appendChild(host);
  win.TopoGraph.renderLayout(host, { nodes: smallChip().nodes, edges: smallChip().edges, layout: null });
  const cmEdge = (id) => host.querySelector('[data-cm="p:' + id + '"]');
  ok(cmEdge('q1-q2') && cmEdge('q1-q2').classList.contains('cm-edge-measured')
     && cmEdge('q1-q3').classList.contains('cm-edge-pending') && cmEdge('q2-q4').classList.contains('cm-edge-none')
     && cmEdge('q3-q4').classList.contains('cm-edge-stale'),
     'component maps style pair edges by the same data rule');
  ok(cmEdge('q1-q2') && cmEdge('q1-q2').getAttribute('data-link-ends') === 'q1 q2'
     && !!host.querySelector('[data-cm="q:q1"][data-link-id="q1"]'),
     'component maps carry the linked-highlight markup');

  console.log(checks + ' checks, ' + fails + ' failures');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: crashed ' + (e && e.stack)); process.exit(1); });
