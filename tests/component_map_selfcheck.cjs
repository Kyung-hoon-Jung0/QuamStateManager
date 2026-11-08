/* Behavioral check for the shared component-page chip layout (docs/92 P2)
 * against the REAL topo-graph.js renderLayout + component-map.js under jsdom.
 *
 * Pins:
 *  - the ONE drawing carries EVERY component type's symbols (stones, pair
 *    edges + arrows + coupler dots, resonator marks, flux stubs, feedline
 *    buses) and NO numbers — the only rendered <text> is the qubit id
 *    (docs/91 §2.4: the table beside it has every number);
 *  - highlight is EMPHASIS only (cm-hl-<mode> class on the svg root; content
 *    identical across modes);
 *  - honesty modes ride layoutFor: logical wears LOGICAL_LAYOUT_NOTE ON the
 *    map, no-layout chips get one honest line (docs/91 §2.1);
 *  - ComponentMap: collapse persisted + lazy-load when closed (docs/91 §6.5),
 *    map↔table hover binding BOTH directions, click opens the inspector, and
 *    a re-mount after an HTMX swap can never stack pane listeners (the
 *    persistent #table-pane holds ONE delegated handler reading the CURRENT
 *    mount).
 *
 * Run: node tests/component_map_selfcheck.cjs   (driven by tests/test_component_map.py)
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
const TOPO_JS = read('topo-graph.js');
const CMAP_JS = read('component-map.js');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

const TOPO = {
  nodes: [
    // f_01: qA2 is 200 MHz ABOVE qA1 -> the qA2-qA1 edge gets a chevron whose
    // apex points at qA1; qA3 has NO f_01 -> its edge gets none (honesty gate)
    { id: 'qA1', grid_location: '0,0', chain: 'A', f_01: 5.0e9, rr_port: 'con1/fem1/p1', z_port: 'con1/fem5/p1' },
    { id: 'qA2', grid_location: '1,0', chain: 'A', f_01: 5.2e9, rr_port: 'con1/fem1/p1', z_port: 'con1/fem5/p2' },
    { id: 'qA3', grid_location: '0,1', chain: 'B', rr_port: 'con1/fem2/p1', z_port: null },
  ],
  edges: [
    { pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1', directed: false, has_coupler: true, active: null },
    { pair_id: 'qA1-qA3', source: 'qA1', target: 'qA3', directed: true, has_coupler: false, active: true, moving_qubit: 'control' },
  ],
};

function makeWorld(bodyHtml) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + bodyHtml + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._htmxCalls = [];
  win.htmx = { ajax: function () { win._htmxCalls.push(Array.prototype.slice.call(arguments)); } };
  win._fetchUrls = [];
  win.fetch = function (url) {
    win._fetchUrls.push(String(url));
    return win.Promise.resolve({ ok: true, json: function () { return win.Promise.resolve(JSON.parse(JSON.stringify(TOPO))); } });
  };
  new win.Function(TOPO_JS + '\n;\n' + CMAP_JS).call(win);
  return win;
}

const tick = () => new Promise((r) => setTimeout(r, 0));

// ── renderLayout: the drawing itself ────────────────────────────────────────
function checkRenderLayout() {
  const win = makeWorld('<div id="m"></div>');
  const m = win.document.getElementById('m');
  const api = win.TopoGraph.renderLayout(m, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: 'pairs' });

  ok(api.mode === 'physical', 'renderLayout: physical mode on a full grid');
  const svg = m.querySelector('svg.cm-svg');
  ok(!!svg, 'renderLayout: svg rendered');
  ok(svg.getAttribute('class').indexOf('cm-hl-pairs') !== -1, 'highlight mode rides the svg root class');
  ok(m.querySelectorAll('.cm-stone').length === 3, 'all 3 stones drawn');
  ok(m.querySelectorAll('.cm-edge').length === 2, 'both pair edges drawn');
  // Map tidy: roles are the shared C / T / M badges; no blue arrow, no open ring
  ok(m.querySelectorAll('.cm-dir-arrow, .cm-dir-m').length === 0, 'no control -> target arrow and no moving ring left');
  {
    const at = (id) => { const c = m.querySelector('[data-cm="q:' + id + '"] .cm-stone'); return [+c.getAttribute('cx'), +c.getAttribute('cy')]; };
    const cen = (sel) => { const c = m.querySelector(sel + ' circle'); return c ? [+c.getAttribute('cx'), +c.getAttribute('cy')] : [1e9, 1e9]; };
    const dist = (p, id) => Math.hypot(p[0] - at(id)[0], p[1] - at(id)[1]);
    const C = cen('.cm-role-c[data-cm-pair="qA1-qA3"]'), T = cen('.cm-role-t[data-cm-pair="qA1-qA3"]');
    ok(dist(C, 'qA1') < dist(C, 'qA3') && dist(T, 'qA3') < dist(T, 'qA1'), 'C at the control (qA1) end, T at the target (qA3) end');
    ok(Math.abs(dist(C, 'qA1') - dist(T, 'qA3')) < 0.2, 'C and T mirrored: the same distance from their own stones');
    const M = cen('.cm-role-m[data-cm-pair="qA1-qA3"]');
    ok(dist(M, 'qA1') < dist(M, 'qA3'), 'M beside the moving (control) qubit');
    ok(!m.querySelector('.cm-role-m[data-cm-pair="qA2-qA1"]'), 'no M where no moving qubit is declared');
    const leg = m.querySelector('.cm-dir-legend');
    ok(!!leg && /control/.test(leg.textContent) && /target/.test(leg.textContent) && /moving qubit/.test(leg.textContent)
       && /toward the lower f/.test(leg.textContent) && leg.querySelectorAll('.cm-role-txt').length === 3,
       'legend: [C] control, [T] target, [M] moving qubit, orange arrow toward the lower f01');
    // badges identical to the hero's: same shared call with compactRoles
    const TGx = win.TopoGraph, pxf = (id) => ({ qA1: { x: 0, y: 0 }, qA3: { x: 0, y: 96 } })[id];
    const ref = TGx.pairGlyphs([], [TOPO.edges[1]], { px: pxf, cell: 64, roles: true, compactRoles: true });
    const r0 = m.querySelector('.cm-role-c[data-cm-pair="qA1-qA3"] circle').getAttribute('r');
    ok(ref.indexOf('r="' + r0 + '"') !== -1, 'component-map badge radius == the hero (compactRoles) radius (' + r0 + ')');
  }
  ok(m.querySelectorAll('.cm-coupler').length === 1, 'coupler dot drawn on the has_coupler edge');
  ok(m.querySelectorAll('.cm-res').length === 3, 'resonator marks for all 3 (all have rr_port)');
  ok(m.querySelectorAll('.cm-flux').length === 2, 'flux stubs only for qubits WITH a z_port');
  const feeds = m.querySelectorAll('.cm-feed');
  ok(feeds.length === 1, 'ONE feedline bus for the shared rr_port group (con1/fem1/p1 x2)');

  // §2.4: NO NUMBERS. docs/120 item 11 added the C/T/M role markers here (the
  // customer asked for ONE convention across SM), so the pin is restated at
  // the invariant it always meant rather than at "text length == qubit count":
  // every rendered string is either a qubit id or a role LETTER, and nothing
  // drawn is a number. Values still live in the table beside the map.
  const texts = m.querySelectorAll('svg text');
  const idSet = { qA1: 1, qA2: 1, qA3: 1 };
  const roleSet = { C: 1, T: 1, M: 1 };
  // P1-11 (UX round): each pair edge carries its pair id -- an id, like the
  // qubit ids, never a value (the Pairs page named pairs only in a hover title)
  const pairSet = { 'qA2-qA1': 1, 'qA1-qA3': 1 };
  ok(m.querySelectorAll('text.cm-id').length === 3,
     'exactly one id text per qubit (no value text anywhere)');
  const pl = Array.prototype.map.call(m.querySelectorAll('.cm-pair-label'), function (t) { return t.getAttribute('data-pair-id') || t.textContent; });
  ok(pl.length === 2 && pairSet[pl[0]] && pairSet[pl[1]] && pl[0] !== pl[1],
     'one pair-id label per pair edge: ' + JSON.stringify(pl));
  let onlyIdsOrRoles = true, anyNumeric = false;
  texts.forEach(function (t) {
    const cls = t.getAttribute('class') || '';
    // Each text in a non-level pair belongs to its shared label group.
    const stacked = t.parentElement.matches('g.cm-dir-stack') && !!idSet[t.textContent];
    const s = stacked ? t.parentElement.getAttribute('data-pair-id') : t.textContent;
    const isId = idSet[s] && cls === 'cm-id';
    const isRole = roleSet[s] && cls.indexOf('cm-role-txt') !== -1;
    const isPair = pairSet[s] && (cls === 'cm-pair-label cm-dir-label' || stacked);
    if (!isId && !isRole && !isPair) onlyIdsOrRoles = false;
    if (/[0-9]/.test(s) && !idSet[s] && !isPair) anyNumeric = true;
  });
  // FB-01: no pair label is ever rotated; a steep edge stacks its two ids.
  const rot = Array.prototype.filter.call(m.querySelectorAll('svg *'), function (t) {
    return /rotate/.test(t.getAttribute('transform') || ''); });
  ok(rot.length === 0, 'no pair label carries a rotate transform: ' + rot.length);
  const st = m.querySelector('g.cm-dir-stack');
  ok(st && Array.prototype.map.call(st.querySelectorAll('text'), function (x) { return x.textContent; }).join(' ') === 'qA3 qA1' && st.querySelectorAll('line.cm-dir-connector').length === 1,
     'a vertical edge groups two ids top-first with an SVG connector: ' + (st && st.textContent));
  ok(onlyIdsOrRoles, 'every rendered text is a qubit id, a pair id or a C/T/M role letter');
  ok(!anyNumeric, 'no NUMBER is ever drawn on the component map (docs/91 §2.4)');

  // emphasis never changes content: same drawing under a different highlight
  const m2 = win.document.createElement('div');
  win.document.body.appendChild(m2);
  win.TopoGraph.renderLayout(m2, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: 'resonators' });
  const strip = (h) => h.replace(/cm-hl-[a-z]+/g, 'cm-hl-X');
  ok(strip(m.innerHTML) === strip(m2.innerHTML),
     'highlight changes EMPHASIS only — drawings byte-identical up to the mode class');

  // F2 (docs/93): text scales with the cell. D-98: the qubit id is one step
  // smaller (x0.88) but never under 12px (D-6); the pair label one step larger.
  const idEl = m.querySelector('.cm-id');
  ok(idEl.getAttribute('font-size') === '12', 'default cell: id font 10.5 x 0.88 floored at 12px');
  ok(m.querySelector('.cm-dir-label').getAttribute('font-size') === '10.8', 'default cell: pair label 9 x 1.2 = 10.8px');
  const mBig = win.document.createElement('div');
  win.document.body.appendChild(mBig);
  win.TopoGraph.renderLayout(mBig, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: 'pairs', cell: 120 });
  const bigSvg = mBig.querySelector('svg');
  ok(parseInt(bigSvg.getAttribute('width'), 10) === 360,
     'cell:120 with x1.5 spacing: 2 cols -> 360 (got ' + bigSvg.getAttribute('width') + ')');
  ok(mBig.querySelector('.cm-stone').getAttribute('r') === '36', 'cell:120 -> 36px stones (0.30 ratio kept)');
  ok(mBig.querySelector('.cm-id').getAttribute('font-size') === '17.3',
     'cell:120 -> 17.3px id font (19.7 x 0.88, D-98)');

  // F5 (docs/93): the frequency-inequality chevron — one per PHYSICAL pair,
  // apex at the lower-f_01 qubit, honest gates, Δ only in the tooltip.
  {
    const freqs = m.querySelectorAll('.cm-freq');
    ok(freqs.length === 1,
       'exactly ONE frequency arrow: qA2-qA1 has both f_01s; qA1-qA3 lacks one (got ' + freqs.length + ')');
    const g = freqs[0];
    ok(/Δf_01: qA2 \+200\.0 MHz vs qA1/.test(g.querySelector('title').textContent),
       'tooltip names the higher qubit and the exact Δ');
    ok(g.querySelectorAll('.cm-freqchev, polyline').length === 0, 'D-3: no chevron markup, an arrow instead');
    // D-3: the arrow's tip sits at the LOWER-f qubit (qA1), tail at the higher
    const tipOf = (root) => root.querySelector('.cm-dir-freq-head').getAttribute('points').split(' ')[2].split(',').map(Number);
    const qA1x = 0.5 * 96, qA2x = 1.5 * 96;   // stone centres, default cell, x1.5 spacing (D-94)
    ok(Math.abs(tipOf(g)[0] - qA1x) < Math.abs(tipOf(g)[0] - qA2x), 'D-3: frequency arrow points to the LOWER-f qubit (qA1)');
    // D-3: same stroke + same head as the control arrow, and the same length as its visible run
    const fl = g.querySelector('.cm-dir-freq-line'), fh = g.querySelector('.cm-dir-freq-head').getAttribute('points');
    const da = '0,0 0,13.86 12.6,6.93';   // the old control arrow head at the default cell (12.6 long, 13.86 wide)
    const shape = (pts) => { const p = pts.split(' ').map((q) => q.split(',').map(Number));
      return [Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]),
              Math.hypot(p[2][0] - (p[0][0] + p[1][0]) / 2, p[2][1] - (p[0][1] + p[1][1]) / 2)]; };
    ok(Math.abs(shape(fh)[0] - shape(da)[0]) < 0.25 && Math.abs(shape(fh)[1] - shape(da)[1]) < 0.25, 'D-3: frequency head == control head (' + shape(fh) + ' vs ' + shape(da) + ')');
    const ft = tipOf(g), dt = da.split(' ')[2].split(',').map(Number);
    const fRun = Math.hypot(+fl.getAttribute('x1') - ft[0], +fl.getAttribute('y1') - ft[1]);
    ok(Math.abs(fRun - (96 - 2 * (0.30 * 64 + 3))) < 0.6, 'D-3: arrow spans stone rim+3 to rim+3 like the control arrow (' + fRun.toFixed(1) + ')');
    const kit2 = read('ui-kit.css');
    ok(/\.cm-freq \.cm-dir-freq-line \{ stroke: var\(--ui-freq-arrow\); stroke-width: 4\.5px;/.test(kit2), 'D-3: arrow stroke 4.5px like the direction line, own token');
    // reversed frequencies flip it
    const mRev = win.document.createElement('div');
    win.document.body.appendChild(mRev);
    win.TopoGraph.renderLayout(mRev, {
      nodes: [{ id: 'qA1', grid_location: '0,0', f_01: 5.2e9 },
              { id: 'qA2', grid_location: '1,0', f_01: 5.0e9 }],
      edges: [{ pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1' }], highlight: 'pairs',
    });
    ok(/Δf_01: qA1 \+200\.0 MHz vs qA2/.test(mRev.querySelector('.cm-freq title').textContent),
       'flipped frequencies flip the arrow (qA1 now higher)');
    ok(Math.abs(tipOf(mRev)[0] - qA2x) < Math.abs(tipOf(mRev)[0] - qA1x), 'flipped: tip now at qA2, opposite the control arrow');
    // CR both-directions dedupe + near-equal gate
    const mCr = win.document.createElement('div');
    win.document.body.appendChild(mCr);
    win.TopoGraph.renderLayout(mCr, {
      nodes: [{ id: 'q1', grid_location: '0,0', f_01: 5.0e9 },
              { id: 'q2', grid_location: '1,0', f_01: 5.1e9 },
              { id: 'q3', grid_location: '2,0', f_01: 5.1e9 + 5e5 }],
      edges: [
        { pair_id: 'q1-2', source: 'q1', target: 'q2', directed: true },
        { pair_id: 'q2-1', source: 'q2', target: 'q1', directed: true },
        { pair_id: 'q2-3', source: 'q2', target: 'q3' },
      ], highlight: 'pairs',
    });
    ok(mCr.querySelectorAll('.cm-freq').length === 1,
       'anti-parallel CR directions share ONE frequency arrow, and |Δ| < 1 MHz draws none');
  }
  // ---- map tidy (D-1, D-2, D-3 gate, D-4) ----
  {
    const TG = win.TopoGraph;
    // D-4: pair label ~0.87x at a large cell, arrowhead 1.4x, line 1.5x stroke
    ok(mBig.querySelector('.cm-dir-label').getAttribute('font-size') === '17.7',
       'D-4: cell:120 pair label 20.3 x 0.87 = 17.7px (got ' + mBig.querySelector('.cm-dir-label').getAttribute('font-size') + ')');
    const ap = mBig.querySelector('.cm-dir-freq-head').getAttribute('points').split(' ').map((p) => p.split(',').map(Number));
    const bx = (ap[0][0] + ap[1][0]) / 2, by = (ap[0][1] + ap[1][1]) / 2;
    const ahLen = Math.hypot(ap[2][0] - bx, ap[2][1] - by);
    ok(Math.abs(ahLen - Math.max(9, 36 * 0.36) * 1.4) < 0.3, 'D-4: frequency arrowhead length scaled 1.4x (got ' + ahLen.toFixed(2) + ')');
    // D-1: one screen side whatever the direction: above a level edge, left of a vertical one
    const lab = (ang) => { const t = win.document.createElementNS('http://www.w3.org/2000/svg', 'text'); t.setAttribute('data-ang', ang); return t; };
    const side = (ang) => { const t = lab(ang); const a = ang * Math.PI / 180, sd = TG.screenSide(t);
      return [-Math.sin(a) * sd, Math.cos(a) * sd]; };
    ok(side(0)[1] < 0 && side(180)[1] < 0, 'D-1: both directions of a level edge put the label ABOVE');
    ok(side(90)[0] < 0 && side(-90)[0] < 0, 'D-1: both directions of a vertical edge put the label LEFT');
    // D-2: M sits ON the line (mirrored along the edge), for both edge orientations
    const px = (id) => ({ a: { x: 0, y: 0 }, b: { x: 100, y: 0 }, c: { x: 0, y: 100 } })[id];
    const host = win.document.createElement('div');
    host.innerHTML = '<svg>' + TG.pairGlyphs([], [
      { pair_id: 'a-b', source: 'a', target: 'b', moving_qubit: 'control' },
      { pair_id: 'c-a', source: 'c', target: 'a', moving_qubit: 'target' }], { px, cell: 64, roles: true }) + '</svg>';
    const mAB = host.querySelector('.cm-role-m[data-cm-pair="a-b"] circle');
    const mCA = host.querySelector('.cm-role-m[data-cm-pair="c-a"] circle');
    ok(mAB && +mAB.getAttribute('cy') === 0 && +mAB.getAttribute('cx') < 50, 'D-2: level M on the line, on its own half');
    ok(mCA && +mCA.getAttribute('cx') === 0 && +mCA.getAttribute('cy') < 50, 'D-2: vertical M on the line, on its own half');
    // D-3: freqArrows:false (the hero's Qubit-freq view) draws none
    const nodesF = [{ id: 'a', f_01: 5.2e9 }, { id: 'b', f_01: 5.0e9 }];
    ok(TG.pairGlyphs(nodesF, [{ source: 'a', target: 'b' }], { px, cell: 64 }).indexOf('cm-dir-freq-line') !== -1, 'D-3: arrow drawn by default');
    ok(TG.pairGlyphs(nodesF, [{ source: 'a', target: 'b' }], { px, cell: 64, freqArrows: false }).indexOf('cm-freq') === -1, 'D-3: freqArrows:false draws none');
    // D-3 screen side: below a level edge, right of a vertical one, whichever end is lower
    const nodesG = [{ id: 'a', f_01: 5.0e9 }, { id: 'b', f_01: 5.2e9 }, { id: 'c', f_01: 5.4e9 }];
    const hostS = win.document.createElement('div');
    hostS.innerHTML = '<svg>' + TG.pairGlyphs(nodesF.concat([{ id: 'c', f_01: 5.4e9 }]), [{ source: 'a', target: 'b' }, { source: 'a', target: 'c' }], { px, cell: 64, freqSide: 'screen' }) +
      TG.pairGlyphs(nodesG, [{ source: 'a', target: 'b' }, { source: 'a', target: 'c' }], { px, cell: 64, freqSide: 'screen' }) + '</svg>';
    const ls = hostS.querySelectorAll('.cm-dir-freq-line');
    ok(+ls[0].getAttribute('y1') > 0 && +ls[2].getAttribute('y1') > 0, 'D-3: level arrows below the line in both frequency orders');
    ok(+ls[1].getAttribute('x1') > 0 && +ls[3].getAttribute('x1') > 0, 'D-3: vertical arrows right of the line in both orders');
  }

  // F4 (docs/93): feedline slots assigned in SPATIAL order (screen top first),
  // NOT declaration order — palette adjacency must equal screen adjacency.
  {
    const mF = win.document.createElement('div');
    win.document.body.appendChild(mF);
    const apiF = win.TopoGraph.renderLayout(mF, {
      nodes: [
        // BOTTOM feedline declared FIRST — must still take slot 2
        { id: 'qX1', grid_location: '0,0', rr_port: 'pBottom' },
        { id: 'qX2', grid_location: '1,0', rr_port: 'pBottom' },
        { id: 'qY1', grid_location: '0,1', rr_port: 'pTop' },
        { id: 'qY2', grid_location: '1,1', rr_port: 'pTop' },
      ],
      edges: [], highlight: 'resonators',
    });
    ok(JSON.stringify(apiF.feeds) === JSON.stringify([
      { label: 'pTop', slot: 1, count: 2 }, { label: 'pBottom', slot: 2, count: 2 }]),
      'slots follow SCREEN order (top bus = slot 1) regardless of declaration order — got ' +
      JSON.stringify(apiF.feeds));
    ok(mF.querySelector('polyline.cm-feed-s1').getAttribute('data-cm-feed') === 'pTop',
      'the top bus polyline wears slot 1');
    const y1res = mF.querySelectorAll('.cm-node')[2].querySelector('.cm-res');
    ok(y1res && y1res.getAttribute('class').indexOf('cm-feed-s1') !== -1,
      'resonator marks inherit their bus slot');
    // 8 buses: the 8th degrades to the neutral slot 0 with a unique dash — never an 8th hue
    const many = [];
    for (let g = 0; g < 8; g++) {
      many.push({ id: 'qm' + g + 'a', grid_location: '0,' + g, rr_port: 'line' + g });
      many.push({ id: 'qm' + g + 'b', grid_location: '1,' + g, rr_port: 'line' + g });
    }
    const mMany = win.document.createElement('div');
    win.document.body.appendChild(mMany);
    const apiMany = win.TopoGraph.renderLayout(mMany, { nodes: many, edges: [], highlight: 'resonators' });
    const slots = apiMany.feeds.map((f) => f.slot);
    ok(JSON.stringify(slots) === JSON.stringify([1, 2, 3, 4, 5, 6, 7, 0]),
      '8th bus wears the neutral slot 0 (hues never cycle) — got ' + JSON.stringify(slots));
    ok(/stroke-dasharray/.test(mMany.querySelector('polyline.cm-feed-s0').getAttribute('style') || ''),
      'the neutral bus carries its own dash as inline STYLE (a stylesheet rule beats attributes)');
  }

  // F3 (docs/93): chain emphasis — matching stones read selected, the rest
  // (with their satellite marks) drop to context; absent by default.
  ok(!m.querySelector('.cm-chain-emph, .cm-chain-dim'),
     'no emphasisChain -> no chain classes anywhere');
  const mCh = win.document.createElement('div');
  win.document.body.appendChild(mCh);
  win.TopoGraph.renderLayout(mCh, { nodes: TOPO.nodes, edges: TOPO.edges,
                                    highlight: 'qubits', emphasisChain: 'A' });
  ok(mCh.querySelectorAll('.cm-node.cm-chain-emph').length === 2,
     'emphasisChain A lights the two chain-A stones');
  ok(mCh.querySelectorAll('.cm-node.cm-chain-dim').length === 1,
     'the chain-B stone drops to context');
  ok(mCh.querySelector('[data-cm="q:qA3"]').getAttribute('data-cm-chain') === 'B',
     'stones carry their chain');

  // entity hover hook
  api.highlightEntity('pair', 'qA2-qA1', true);
  const hotEdge = m.querySelector('[data-cm="p:qA2-qA1"]');
  ok(hotEdge && hotEdge.classList.contains('cm-hot'), 'highlightEntity lights the pair group');
  api.highlightEntity('pair', 'qA2-qA1', false);
  ok(hotEdge && !hotEdge.classList.contains('cm-hot'), 'highlightEntity clears');

  // logical + none honesty
  const m3 = win.document.createElement('div');
  win.document.body.appendChild(m3);
  const apiL = win.TopoGraph.renderLayout(m3, {
    nodes: [{ id: 'q1' }, { id: 'q2' }], edges: [['q1', 'q2']], highlight: 'pairs' });
  ok(apiL.mode === 'logical', 'grid-less chip renders logical');
  const note = m3.querySelector('.cm-note');
  ok(!!note && note.textContent === win.TopoGraph.LOGICAL_LAYOUT_NOTE,
     'logical layout wears LOGICAL_LAYOUT_NOTE ON the map');
  const m4 = win.document.createElement('div');
  win.document.body.appendChild(m4);
  const apiN = win.TopoGraph.renderLayout(m4, { nodes: [{ id: 'q1' }], edges: [], highlight: 'qubits' });
  ok(apiN.mode === 'none' && !m4.querySelector('svg') && /No chip layout/.test(m4.textContent),
     'no positions + no pairs -> honest line, no svg');
}

// ── ComponentMap: mount, binding, swap-safety ───────────────────────────────
const PANE_HTML =
  '<div id="table-pane">'
  + '<details class="cmap" id="component-map" data-highlight="pairs" open>'
  + '<summary>Chip layout</summary><div class="cmap-body"></div></details>'
  + '<table><thead><tr><th>ID</th><th>P(RO) <span class="unit phys-unit-label">(dBm)</span></th></tr></thead><tbody>'
  + '<tr data-pair-id="qA2-qA1"><td>pair row</td><td colspan="1">x</td></tr>'
  + '<tr data-qubit-id="qA1"><td>qubit row</td><td data-dbm="-20">-20.0 dBm</td></tr>'
  + '</tbody></table>'
  + '</div><div id="inspector-pane"></div>';

async function checkComponentMap() {
  const win = makeWorld(PANE_HTML);
  const doc = win.document;
  win.ComponentMap.mount(doc.getElementById('component-map'));
  await tick(); await tick();

  const body = doc.querySelector('.cmap-body');
  ok(!!body.querySelector('svg.cm-svg'), 'mount fetches /api/topology and renders the layout');
  // v1.3.0 (perf): the map asks for the layout alone -- never the Chip Status
  // enrichment whose RB run lookup scans the Datasets index on a cold open
  ok(win._fetchUrls.length === 1 && win._fetchUrls[0] === '/api/topology?layout=1',
     'the map requests the structure-only topology (got ' + JSON.stringify(win._fetchUrls) + ')');

  // map -> table: hovering a stone lights its row
  const stone = body.querySelector('[data-cm="q:qA1"]');
  stone.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const qrow = doc.querySelector('tr[data-qubit-id="qA1"]');
  ok(qrow.classList.contains('cm-row-hot'), 'map hover lights the matching table row');
  body.dispatchEvent(new win.MouseEvent('mouseleave'));
  ok(!qrow.classList.contains('cm-row-hot'), 'leaving the map clears the row');

  // table -> map: hovering a row lights the entity
  const prow = doc.querySelector('tr[data-pair-id="qA2-qA1"]');
  prow.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const edge = body.querySelector('[data-cm="p:qA2-qA1"]');
  ok(edge.classList.contains('cm-hot'), 'row hover lights the map entity');
  doc.getElementById('table-pane').dispatchEvent(new win.MouseEvent('mouseleave'));
  ok(!edge.classList.contains('cm-hot'), 'leaving the pane clears the entity');

  // click a stone -> inspector
  stone.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(win._htmxCalls.some(function (c) { return c[1] === '/qubit/qA1'; }),
     'clicking a map entity opens its inspector');

  // v1.5.2: hover card -> explicit Expand, so maps stay visible.
  // docs/109 (3): the hover card — the entity's table row as a floating
  // summary (audit: mutation-testing showed the popup had ZERO coverage).
  stone.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  stone.dispatchEvent(new win.MouseEvent('mouseenter'));
  ok(!doc.querySelector('.cm-popup:not([hidden])'), 'hover offers no detail panel');
  doc.querySelector('.topo-expand').click();
  let pop = doc.querySelector('.cm-popup');
  ok(!!pop && !pop.hidden, 'hovering a stone shows the summary card');
  ok(pop.querySelector('.cm-popup-title').firstChild.textContent === 'qA1',
     'the card is titled with the entity id');
  const rows = pop.querySelectorAll('.cm-popup-row');
  ok(rows.length === 1 && rows[0].querySelector('.cm-popup-k').textContent.indexOf('P(RO)') === 0
     && rows[0].querySelector('.cm-popup-v').textContent === '-20.0 dBm',
     'the card pairs the row cells with their thead labels (col 0 skipped)');
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  ok(pop.hidden, 'outside click closes the card');
  // an entity with NO matching row still gets the minimal card
  const edgeNoRow = body.querySelector('[data-cm="p:qA2-qA1"]');
  const prowEl = doc.querySelector('tr[data-pair-id="qA2-qA1"]');
  prowEl.parentNode.removeChild(prowEl);
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  doc.querySelector('.topo-expand').click();
  pop = doc.querySelector('.cm-popup');
  ok(!pop.hidden && pop.querySelector('.cm-popup-title').firstChild.textContent === 'pair qA2-qA1'
     && pop.querySelectorAll('.cm-popup-row').length === 0,
     'a row-less entity gets the minimal title-only card');

  // HTMX swap: replace the pane content, re-mount — binding still single
  const pane = doc.getElementById('table-pane');
  pane.innerHTML = PANE_HTML.replace('<div id="table-pane">', '').replace('</div><div id="inspector-pane"></div>', '');
  win.ComponentMap.mount(doc.getElementById('component-map'));
  await tick(); await tick();
  const body2 = pane.querySelector('.cmap-body');
  ok(!!body2.querySelector('svg.cm-svg'), 're-mount after a swap renders again');
  ok(!doc.querySelector('.cm-popup:not([hidden])'),
     'a re-mount hides any lingering hover card (audit: swap under a motionless cursor)');
  const prow2 = pane.querySelector('tr[data-pair-id="qA2-qA1"]');
  prow2.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  ok(body2.querySelectorAll('.cm-hot').length === 1,
     'after re-mount exactly ONE entity lights (no stacked pane listeners, current api wins)');

  // collapse persistence: saved-closed -> mount respects it and does NOT fetch
  const win2 = makeWorld(PANE_HTML);
  win2.localStorage.setItem('quam_component_map_open', '0');
  let fetches = 0;
  const origFetch = win2.fetch;
  win2.fetch = function () { fetches++; return origFetch.apply(this, arguments); };
  win2.ComponentMap.mount(win2.document.getElementById('component-map'));
  await tick(); await tick();
  const root2 = win2.document.getElementById('component-map');
  ok(!root2.hasAttribute('open'), 'saved-closed collapse is restored on mount');
  ok(fetches === 0, 'a collapsed map does not fetch (lazy)');
  // a person's toggle: the summary click comes first (R4b UD-3 -- a parsed
  // <details open> fires toggle by itself, which is no choice)
  const sum2 = root2.querySelector('summary');
  if (sum2) sum2.dispatchEvent(new win2.Event('click'));
  else root2._cmapByUser = true;
  root2.setAttribute('open', 'open');
  root2.dispatchEvent(new win2.Event('toggle'));
  await tick(); await tick();
  ok(fetches === 1, 'opening it loads exactly once');
  ok(win2.localStorage.getItem('quam_component_map_open') === 'open', 'the choice persists');

  // WC-3's first-screen fit is pinned by chip_status_r4b_selfcheck.cjs (fitUnsized).

  // F2: the mount's data-cell reaches renderLayout through ComponentMap
  const win3 = makeWorld(PANE_HTML.replace('data-highlight="pairs"', 'data-highlight="pairs" data-cell="120"'));
  win3.ComponentMap.mount(win3.document.getElementById('component-map'));
  await tick(); await tick();
  const bigStone = win3.document.querySelector('.cmap-body .cm-stone');
  ok(bigStone && bigStone.getAttribute('r') === '36',
     'data-cell="120" flows through ComponentMap.mount (36px stones)');

  // F3: the mount's data-chain reaches renderLayout through ComponentMap
  const win4 = makeWorld(PANE_HTML.replace('data-highlight="pairs"', 'data-highlight="qubits" data-chain="A"'));
  win4.ComponentMap.mount(win4.document.getElementById('component-map'));
  await tick(); await tick();
  ok(win4.document.querySelectorAll('.cmap-body .cm-node.cm-chain-emph').length === 2 &&
     win4.document.querySelectorAll('.cmap-body .cm-node.cm-chain-dim').length === 1,
     'data-chain="A" flows through ComponentMap.mount (2 emph / 1 dim)');

  // F4: the feedline legend renders on the resonators page only, via DOM
  // APIs (hostile port labels stay text)
  const win5 = makeWorld(PANE_HTML.replace('data-highlight="pairs"', 'data-highlight="resonators"'));
  win5.ComponentMap.mount(win5.document.getElementById('component-map'));
  await tick(); await tick();
  const legend = win5.document.querySelector('.cmap-body .cm-feed-legend');
  ok(!!legend, 'resonators page renders the feedline legend');
  ok(legend && /con1\/fem1\/p1 · 2 resonators/.test(legend.textContent),
     'legend names the bus port label + count');
  ok(legend && legend.querySelectorAll('.cm-feed-lg').length === 1,
     'single-member rr groups draw no bus and get no legend row');
  const win6 = makeWorld(PANE_HTML);   // pairs highlight
  win6.ComponentMap.mount(win6.document.getElementById('component-map'));
  await tick(); await tick();
  ok(!win6.document.querySelector('.cm-feed-legend'), 'no legend outside the resonators page');
  // injection: a wiring-borne hostile label must stay text
  const winX = makeWorld(PANE_HTML.replace('data-highlight="pairs"', 'data-highlight="resonators"'));
  const hostile = '<img src=x onerror=window._pwn=1>';
  winX.fetch = function () {
    return winX.Promise.resolve({ ok: true, json: function () {
      return winX.Promise.resolve({ nodes: [
        { id: 'q1', grid_location: '0,0', rr_port: hostile },
        { id: 'q2', grid_location: '1,0', rr_port: hostile },
      ], edges: [] });
    } });
  };
  winX.ComponentMap.mount(winX.document.getElementById('component-map'));
  await tick(); await tick();
  const lgX = winX.document.querySelector('.cm-feed-legend');
  ok(lgX && lgX.textContent.indexOf(hostile) !== -1 && !winX.document.querySelector('.cm-feed-legend img'),
     'hostile port labels render as TEXT in the legend (no element injection)');
}

(async function main() {
  try {
    checkRenderLayout();
    await checkComponentMap();
  } catch (e) {
    console.error('FAIL: uncaught — ' + (e && e.stack || e));
    fails++;
  }
  if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
  console.log('component_map_selfcheck: all checks passed');
})();
