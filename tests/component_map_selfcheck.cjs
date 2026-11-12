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
    if (String(url).indexOf('/api/pair-map-panel/') === 0) {
      let rep = win._pairPanelReply;
      if (typeof rep === 'function') rep = rep(String(url));
      if (rep && rep.reject) return win.Promise.reject(new win.Error('network'));
      const status = rep && rep.status ? rep.status : (rep ? 200 : 404);
      const body = rep && rep.body !== undefined ? rep.body : (rep || {});
      const res = { ok: status < 400, status: status, json: function () { return win.Promise.resolve(body); } };
      return new win.Promise(function (r) { setTimeout(function () { r(res); }, (rep && rep.delay) || 0); });
    }
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
    const pills = m.querySelectorAll('.cm-role');
    ok(pills.length === 4 && Array.from(pills).every(p => p.querySelectorAll('rect').length === 1 && !p.querySelector('circle')), 'D2: one rounded pill per end, no separate circles');
    ok(m.querySelector('.cm-role-c[data-cm-pair="qA1-qA3"] text').textContent === 'C M', 'D2: moving role inside its end pill');
    const legend = m.querySelector('.cm-dir-legend').textContent;
    ok(/C control.*T target.*M moving qubit.*H higher f01.*L lower f01/.test(legend), 'D6: shared five-letter legend');
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
  const roleSet = { 'C': 1, 'T': 1, 'C H': 1, 'C L': 1, 'T H': 1, 'T L': 1, 'C M': 1, 'T M': 1, 'C \u00b7': 1, 'T \u00b7': 1, 'C M H': 1, 'C M L': 1, 'T M H': 1, 'T M L': 1, 'C \u00b7 H': 1, 'C \u00b7 L': 1, 'T \u00b7 H': 1, 'T \u00b7 L': 1 };
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
  // (the qubits and pairs pages size their own subject; every other page
  // draws the same picture).
  const mk = (hl) => { const d = win.document.createElement('div'); win.document.body.appendChild(d);
    win.TopoGraph.renderLayout(d, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: hl }); return d; };
  const m2 = mk('resonators'), mFx = mk('flux'), mq = mk('qubits');
  const strip = (h) => h.replace(/cm-hl-[a-z]+/g, 'cm-hl-X');
  ok(strip(mFx.innerHTML) === strip(m2.innerHTML),
     'highlight changes EMPHASIS only — drawings byte-identical up to the mode class');
  // Subject sizing: the qubits page draws stones ~2x; the pairs page keeps
  // the stones and enlarges the pair label and the role pills.
  const rOf = (d) => +d.querySelector('.cm-stone').getAttribute('r');
  ok(rOf(m2) === 19 && rOf(m) === 19 && rOf(mq) === 37,
     'stone radius: default 19, pairs 19, qubits 37: ' + [rOf(m2), rOf(m), rOf(mq)]);
  ok(mq.querySelector('.cm-id').getAttribute('font-size') === '19.2', 'qubits page: id font 12 x 1.6');
  ok(m2.querySelector('.cm-dir-label').getAttribute('font-size') === '10.8', 'default page: pair label 10.8px');
  const rfs = (d) => +d.querySelector('.cm-role-txt').getAttribute('font-size');
  ok(rfs(m) > rfs(m2), 'pairs page: role pills larger than the default: ' + [rfs(m), rfs(m2)]);
  const qr = mq.querySelector('.cm-role-dot'), qs = mq.querySelector('.cm-stone');
  ok(qr && qs, 'qubits page still draws role pills');
  // At the cell the component pages mount (120), the qubits page's pills are the largest.
  const mk120 = (hl) => { const d = win.document.createElement('div'); win.document.body.appendChild(d);
    win.TopoGraph.renderLayout(d, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: hl, cell: 120 }); return d; };
  const q120 = mk120('qubits'), p120 = mk120('pairs');
  ok(rfs(q120) > rfs(p120), 'qubits page: role pills larger than the pairs page: ' + [rfs(q120), rfs(p120)]);

  // F2 (docs/93): text scales with the cell. D-98: the qubit id is one step
  // smaller (x0.88) but never under 12px (D-6); the pair label one step larger.
  const idEl = m.querySelector('.cm-id');
  ok(idEl.getAttribute('font-size') === '12', 'default cell: id font 10.5 x 0.88 floored at 12px');
  ok(m.querySelector('.cm-dir-label').getAttribute('font-size') === '14', 'pairs page: pair label 10.8 x 1.3 = 14px');
  const mBig = win.document.createElement('div');
  win.document.body.appendChild(mBig);
  win.TopoGraph.renderLayout(mBig, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: 'pairs', cell: 120 });
  const bigSvg = mBig.querySelector('svg');
  ok(parseInt(bigSvg.getAttribute('width'), 10) === 480,
     'cell:120 with x2 spacing: 2 cols -> 480 (got ' + bigSvg.getAttribute('width') + ')');
  ok(mBig.querySelector('.cm-stone').getAttribute('r') === '36', 'cell:120 -> 36px stones (0.30 ratio kept)');
  ok(mBig.querySelector('.cm-id').getAttribute('font-size') === '17.3',
     'cell:120 -> 17.3px id font (19.7 x 0.88, D-98)');

  // D8: compare the actual component-map call with the hero's default sizing.
  const heroRoles = win.document.createElement('div');
  heroRoles.innerHTML = '<svg>' + win.TopoGraph.pairGlyphs(TOPO.nodes, TOPO.edges,
    {roles:true, cell:120, px:id=>({x:id === TOPO.nodes[0].id ? 0 : 180,y:0})}) + '</svg>';
  const heroFont = +heroRoles.querySelector('.cm-role-txt').getAttribute('font-size');
  // pairpill: floors for the enlarged pill and the thicker pair line.
  ok(heroFont >= 7.5 && +heroRoles.querySelector('.cm-role-dot').getAttribute('height') >= 12,
    'pairpill: hero pill font >= 7.5 and height >= 12 where room allows');
  ok(Array.from(mBig.querySelectorAll('.cm-role-txt')).some(t=>+t.getAttribute('font-size') >= 11),
    'pairpill: component-map pill font reaches >= 11 where room allows');
  { const mm = /^\.cm-edge-line \{[^}]*stroke-width: ([0-9.]+)/m.exec(read('style.css'));
    ok(mm && +mm[1] >= 8, "pairpill: pair edge line stroke >= 8"); }
  { const byPair = {};
    mBig.querySelectorAll('.cm-role').forEach(g => (byPair[g.getAttribute('data-cm-pair')] ||= []).push(+g.querySelector('rect').getAttribute('width')));
    ok(new Set(Object.values(byPair).flat()).size === 1, 'pairpill: every pill on a map has one width');
    ok(/\.cm-dir\.cm-hl-pairs \.cm-res, \.cm-dir\.cm-hl-qubits \.cm-res,\s*\.cm-dir\.cm-hl-pairs \.cm-feed, \.cm-dir\.cm-hl-qubits \.cm-feed,\s*\.cm-dir\.cm-hl-pairs \.cm-flux, \.cm-dir\.cm-hl-qubits \.cm-flux \{ display: none; \}/.test(read('ui-kit.css')), 'pairpill: off-centre readout ring hidden on pair/qubit maps'); }
  for (const map of [m, mBig]) {
    ok(Array.from(map.querySelectorAll('.cm-role-txt')).every(t=>
      +t.getAttribute('font-size') >= 9 && +t.getAttribute('font-size') > heroFont),
      'D8: component-map pills have a readable font larger than the hero');
    ok(Array.from(map.querySelectorAll('.cm-role')).every(role=>{
      const stone = Array.from(map.querySelectorAll('[data-cm]')).find(s=>
        s.getAttribute('data-cm') === 'q:' + role.getAttribute('data-cm-at')).querySelector('.cm-stone');
      const rect = role.querySelector('rect'), n=(el,a)=>+el.getAttribute(a);
      const dx=Math.max(n(rect,'x')-n(stone,'cx'),0,n(stone,'cx')-n(rect,'x')-n(rect,'width'));
      const dy=Math.max(n(rect,'y')-n(stone,'cy'),0,n(stone,'cy')-n(rect,'y')-n(rect,'height'));
      return Math.hypot(dx,dy) >= n(stone,'r') + 1.9;
    }), 'D8: enlarged component pills clear their stones');
  }

  // D1-D5: execute the shared renderer with exact, tiny and missing differences.
  {
    const TG = win.TopoGraph, host = win.document.createElement('div');
    const px = id => ({ a: {x:0,y:0}, b: {x:132,y:0}, c: {x:0,y:132} })[id];
    const edge = {pair_id:'a-b',source:'a',target:'b',moving_qubit:'control'};
    function draw(fa, fb, cell=132, e=edge) {
      host.innerHTML = '<svg>' + TG.pairGlyphs([{id:'a',f_01:fa},{id:'b',f_01:fb}], [e], {px,cell,stoneRadius:37,roles:true}) + '</svg>';
      return Array.from(host.querySelectorAll('.cm-role text')).map(t=>t.textContent).join('|');
    }
    ok(!m.querySelector('.cm-freq, .cm-dir-freq-head') && !read('ui-kit.css').includes('--ui-freq-arrow'), 'D1: orange arrow and unused token removed');
    ok(draw(5e9+1,5e9) === 'C M H|T \u00b7 L', 'D3: even a 1 Hz difference shows both H and L');
    ok(draw(5e9,5e9+1) === 'C M L|T \u00b7 H', 'D3: reversed frequency order');
    {
      draw(5e9+1,5e9);
      const gap = host.querySelectorAll('.cm-role-t text tspan.cm-dir-gap');
      ok(gap.length === 1 && gap[0].textContent === '\u00b7' && !host.querySelector('.cm-role-c tspan'), 'P1: non-moving end has a dot in the M slot, moving end has none');
      const ws = Array.from(host.querySelectorAll('.cm-role rect')).map(r => r.getAttribute('width'));
      ok(ws.length === 2 && ws[0] === ws[1], 'P1: both pills share one width');
      const css = read('ui-kit.css');
      ok(/\.cm-dir-gap \{ fill: var\(--ui-text-3\)/.test(css), 'P1: dot uses the muted ui-kit token');
    }
    for (const value of [5e9, null, '5000000000', NaN, Infinity]) {
      ok(draw(5e9,value) === 'C M|T \u00b7', 'D3: equal or nonnumeric frequency omits H/L: '+value);
    }
    draw(5.1e9,5e9);
    const rects = Array.from(host.querySelectorAll('rect'));
    const left = +rects[0].getAttribute('x') + +rects[0].getAttribute('width');
    const right = +rects[1].getAttribute('x');
    ok(left < 66-4 && right > 66+4 && left + 12 < right, 'D4/D5: line and midpoint metric dot have room between pills');
    ok(+rects[0].getAttribute('x') > 37 && right + +rects[1].getAttribute('width') < 132-37, 'D2: pills just outside stones');
    const size = host.querySelector('text').getAttribute('font-size');
    draw(5.1e9,5e9,528);
    ok(host.querySelector('text').getAttribute('font-size') === size && +size <= 8, 'D4: pill font capped for large cells');
    ok(draw(5.1e9,5e9,132,{...edge,moving_qubit:'target'}) === 'C \u00b7 H|T M L', 'D2: target moving role stays at target');
    ok(draw(5.1e9,5e9,132,{...edge,moving_qubit:null}) === 'C H|T L', 'no known mover: no M slot and no dot');
    draw(5.1e9,5e9,132,edge); ok(/class="cm-dir-gap" aria-hidden="true"/.test(host.innerHTML), 'dot tspan carries aria-hidden');
    draw(5.1e9,5e9,132,{...edge,target:'c'});
    const t = host.querySelector('text');
    ok(+t.getAttribute('x') === 0 && +t.getAttribute('y') > 37, 'D2: vertical pill on line outside stone');
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
  + '<table><thead><tr><th>ID</th><th>P(RO) <span class="unit phys-unit-label">(dBm)</span></th><th>T1</th><th>Grid</th></tr></thead><tbody>'
  + '<tr data-pair-id="qA2-qA1"><td>pair row</td><td colspan="1">x</td><td></td><td></td></tr>'
  + '<tr data-qubit-id="qA1"><td>qubit row</td><td data-dbm="-20">-20.0 dBm</td><td>-</td><td>0,1</td></tr>'
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

  const cmRoot = doc.getElementById('component-map');
  cmRoot.setAttribute('data-highlight', 'qubits');   // the Qubits page: stones get the hover panel
  // ver1.8: hovering shows the panel at once; its Expand button opens the
  // inspector. The panel survives the move into it (grace) and closes on leave.
  const wait = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  win._htmxCalls.length = 0;
  stone.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  stone.dispatchEvent(new win.MouseEvent('mouseenter'));
  let pop = doc.querySelector('.cm-popup');
  ok(!!pop && !pop.hidden, 'hovering a stone shows the panel at once');
  ok(!doc.querySelector('.topo-expand'), 'no separate Expand offer before the panel');
  ok(pop.getAttribute('aria-modal') === 'false', 'the hover panel is non-modal');
  ok(pop.querySelector('.cm-popup-title').firstChild.textContent === 'qA1',
     'the card is titled with the entity id');
  const rows = pop.querySelectorAll('.cm-popup-row');
  ok(rows.length === 1 && rows[0].querySelector('.cm-popup-k').textContent.indexOf('P(RO)') === 0
     && rows[0].querySelector('.cm-popup-v').textContent === '-20.0 dBm',
     'the card pairs cells with their thead labels; absent "-" and Grid rows are hidden ('
     + rows.length + ')');
  const hint = pop.querySelector('.cm-popup-hint');
  ok(rows[0].querySelector('.cm-popup-k').textContent.trim() === 'P(RO)', 'a unit the value carries is not repeated in the label (' + rows[0].querySelector('.cm-popup-k').textContent + ')');
  ok(hint && hint.textContent === 'Expand', 'the panel button reads Expand');
  // leave the stone, enter the panel within the grace: it stays
  stone.dispatchEvent(new win.MouseEvent('mouseleave'));
  await wait(60);
  pop.dispatchEvent(new win.MouseEvent('mouseenter'));
  await wait(320);
  ok(!pop.hidden && pop.isConnected, 'moving from the stone into the panel keeps it open');
  pop.dispatchEvent(new win.MouseEvent('mouseleave'));
  await wait(1000);
  ok(!pop.isConnected || pop.hidden, 'leaving the panel closes it after the grace');
  // placement: the panel goes to the side that covers no neighbouring entity
  (function () {
    const R = (l, t, w, h) => () => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
    const box = (e) => e.querySelector('.cm-stone') || e.querySelector('.cm-edge-hit, .cm-hit') || e;
    const all = Array.prototype.slice.call(body.querySelectorAll('[data-cm]'));
    const saved = all.map((e) => [box(e), box(e).getBoundingClientRect]);
    all.forEach((e) => { box(e).getBoundingClientRect = R(0, 0, 0, 0); });
    box(stone).getBoundingClientRect = R(300, 300, 40, 40);
    const nb = body.querySelector('[data-cm="q:qA2"]');
    box(nb).getBoundingClientRect = R(360, 290, 40, 60);        // the right-hand neighbour
    stone.dispatchEvent(new win.MouseEvent('mouseenter'));
    const pp = doc.querySelector('.cm-popup');
    pp.getBoundingClientRect = R(0, 0, 120, 80);
    pp._placeMode = null;   // place afresh with the stubbed geometry
    win.TopoGraph.positionDetail(pp, stone);
    const x = parseFloat(pp.style.left), y = parseFloat(pp.style.top);
    const hitsNb = x < 400 && x + 120 > 360 && y < 350 && y + 80 > 290;
    const hitsSelf = x < 340 && x + 120 > 300 && y < 340 && y + 80 > 300;
    ok(!hitsNb && !hitsSelf, 'the panel avoids its neighbour and its own stone (' + x + ',' + y + ')');
    box(stone).getBoundingClientRect = R(300, 260, 40, 40);     // the map scrolled 40 px
    box(nb).getBoundingClientRect = R(360, 250, 40, 60);
    doc.dispatchEvent(new win.Event('scroll'));
    ok(parseFloat(pp.style.left) === x && parseFloat(pp.style.top) === y - 40,
       'a scroll moves a beside panel with its stone, same side (' + pp.style.left + ',' + pp.style.top + ')');
    // after the scroll something now sits where the panel is: it still does not jump elsewhere
    box(nb).getBoundingClientRect = R(x, y - 40, 120, 80);
    doc.dispatchEvent(new win.Event('scroll'));
    ok(parseFloat(pp.style.left) === x && parseFloat(pp.style.top) === y - 40,
       'a scroll never re-chooses the spot (' + pp.style.left + ',' + pp.style.top + ')');
    // a pair: clear of the edge AND its two end stones
    cmRoot.setAttribute('data-highlight', 'pairs');
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
    const ed = body.querySelector('[data-cm="p:qA2-qA1"]');
    box(stone).getBoundingClientRect = R(300, 300, 60, 60);
    box(nb).getBoundingClientRect = R(500, 300, 60, 60);
    box(ed).getBoundingClientRect = R(360, 329, 140, 2);
    ed.dispatchEvent(new win.MouseEvent('mouseenter'));
    const pe = doc.querySelector('.cm-popup');
    pe.getBoundingClientRect = R(0, 0, 200, 80);
    pe._placeMode = null;   // place afresh with the stubbed geometry
    win.TopoGraph.positionDetail(pe, ed);
    const px = parseFloat(pe.style.left), py = parseFloat(pe.style.top);
    const overlaps = (l, t, w, h) => px < l + w && px + 200 > l && py < t + h && py + 80 > t;
    ok(!overlaps(300, 300, 60, 60) && !overlaps(500, 300, 60, 60) && !overlaps(360, 329, 140, 2),
       'a pair panel clears the edge and both end stones (' + px + ',' + py + ')');
    delete pe.getBoundingClientRect;
    // no free spot beside the stone: the panel docks away and is tethered to it
    cmRoot.setAttribute('data-highlight', 'qubits');
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
    box(stone).getBoundingClientRect = R(500, 350, 40, 40);
    box(nb).getBoundingClientRect = R(300, 150, 460, 440);    // a crowd all around the stone
    box(ed).getBoundingClientRect = R(0, 0, 0, 0);
    stone.dispatchEvent(new win.MouseEvent('mouseenter'));
    const pd = doc.querySelector('.cm-popup');
    pd.getBoundingClientRect = R(0, 0, 120, 80);
    pd._placeMode = null;   // place afresh with the stubbed geometry
    win.TopoGraph.positionDetail(pd, stone);
    ok(pd._docked === true, 'a crowded stone docks its panel');
    ok(stone.classList.contains('cm-dir-docked'), 'the first docked placement rings its stone');
    // the fallback is an overlay card fixed to the viewport corner: never moved in the DOM
    ok(!body.querySelector('.cm-dir-side') && !body.classList.contains('cm-dir-hasside'), 'no side column: the map keeps its size');
    ok(pd.parentNode === doc.body, 'the panel stays where it was in the DOM (keyboard order)');
    ok(pd.classList.contains('cm-dir-overlay'), 'a panel with no clean spot beside its stone is the corner overlay card');
    ok(!pd.style.maxHeight && !pd.style.left && !pd.style.top, 'the overlay card is placed by CSS, never shrunk inline');
    ok(!!pd.querySelector('.cm-popup-hint'), 'Expand is part of the overlay card');
    ok(doc.body.classList.contains('cm-dir-overlay-on') && /px$/.test(doc.body.style.getPropertyValue('--cm-overlay-h')),
       'an open overlay card lifts the toast lane (body class + its height)');
    ok(!!pd.querySelector('.cm-popup-title .cm-dir-where'), 'the overlay title says where the entity is');
    { const ti = pd.querySelector('.cm-popup-title'), wh = ti.querySelector('.cm-dir-where');
      ok(wh.textContent === 'ringed on the map' && ti.firstChild.nextSibling === wh
         && ti.firstChild.textContent.trim().length > 0,
         'the overlay title reads "<entity> · ringed on the map", the entity first (' + ti.textContent + ')'); }
    doc.dispatchEvent(new win.Event('scroll'));
    ok(pd.classList.contains('cm-dir-overlay') && !pd.style.left && !pd.style.top, 'a scroll does not move the overlay card');
    // a click inside the overlay card keeps it open
    win._htmxCalls.length = 0;
    pd.querySelector('.cm-popup-title').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(!pd.hidden && pd.classList.contains('cm-dir-overlay'), 'clicking inside the overlay card keeps it open');
    ok(!!doc.querySelector('svg.cm-dir-tether .cm-dir-tether-ring') && stone.classList.contains('cm-dir-docked'),
       'a docked panel rings its stone');
    ok(!doc.querySelector('svg.cm-dir-tether line'), 'no connector line (it would read as a pair edge)');
    delete pd.getBoundingClientRect;
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
    ok(!doc.querySelector('svg.cm-dir-tether') && !stone.classList.contains('cm-dir-docked'),
       'closing the panel removes the ring and the line');
    ok(!doc.body.classList.contains('cm-dir-overlay-on') && !doc.body.style.getPropertyValue('--cm-overlay-h'),
       'closing the overlay card drops the toast lift');
    cmRoot.setAttribute('data-highlight', 'qubits');
    delete pp.getBoundingClientRect;
    saved.forEach((p) => { p[0].getBoundingClientRect = p[1]; });
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  })();
  // a docked panel (no free spot beside the anchor): another entity takes over
  // only after the pointer rests on it, so the trip to the panel crosses freely
  await (async function () {
    stone.dispatchEvent(new win.MouseEvent('mouseenter'));
    const pq = doc.querySelector('.cm-popup');
    pq._docked = true;
    const nbr = body.querySelector('[data-cm="q:qA2"]');
    nbr.dispatchEvent(new win.MouseEvent('mouseenter'));
    nbr.dispatchEvent(new win.MouseEvent('mouseleave'));
    await wait(200);
    ok(doc.querySelector('.cm-popup:not([hidden]) .cm-popup-title').firstChild.textContent === 'qA1',
       'crossing a neighbour on the way to a docked panel does not switch');
    nbr.dispatchEvent(new win.MouseEvent('mouseenter'));
    await wait(200);
    ok(doc.querySelector('.cm-popup:not([hidden]) .cm-popup-title').firstChild.textContent === 'qA2',
       'resting on a neighbour switches to it');
    // moving inside the panel never switches (the panel holds no switching logic)
    const p2 = doc.querySelector('.cm-popup');
    const v = p2.querySelector('.cm-popup-row') || p2.querySelector('.cm-popup-title');
    v.dispatchEvent(new win.MouseEvent('mousemove', { bubbles: true, clientX: 10, clientY: 10 }));
    ok(doc.querySelector('.cm-popup:not([hidden]) .cm-popup-title').firstChild.textContent === 'qA2',
       'moving inside the panel keeps it');
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  })();
  // the qubit panel reads its table row: a patched row patches the open panel
  await (async function () {
    stone.dispatchEvent(new win.MouseEvent('mouseenter'));
    const cell = doc.querySelector('tr[data-qubit-id="qA1"] td[data-dbm]');
    const was = cell.textContent;
    cell.textContent = '-21.0 dBm';
    await wait(500);
    const v = doc.querySelector('.cm-popup:not([hidden]) .cm-popup-v');
    ok(v && v.textContent === '-21.0 dBm', 'an open qubit panel follows its patched row (' + (v && v.textContent) + ')');
    cell.textContent = was;
    await wait(150);
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  })();
  // Expand -> inspector
  stone.dispatchEvent(new win.MouseEvent('mouseenter'));
  pop = doc.querySelector('.cm-popup');
  pop.querySelector('.cm-popup-hint').click();
  ok(win._htmxCalls.some(function (c) { return c[1] === '/qubit/qA1'; }), 'Expand opens the inspector');
  ok(!doc.querySelector('.cm-popup:not([hidden])'), 'Expand closes the panel');
  // keyboard: focus shows it, Tab moves into it, Escape closes
  stone.dispatchEvent(new win.FocusEvent('focusin'));
  pop = doc.querySelector('.cm-popup');
  ok(!!pop && !pop.hidden, 'keyboard focus on a stone shows the panel');
  stone.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
  ok(doc.activeElement === pop.querySelector('.cm-popup-hint'), 'Tab from the stone reaches Expand');
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  ok(!doc.querySelector('.cm-popup:not([hidden])') || !pop.isConnected, 'Escape closes the panel');
  // touch: a tap shows the panel, it does not open the inspector
  win._htmxCalls.length = 0;
  stone.dispatchEvent(new win.MouseEvent('pointerdown', { bubbles: true }));
  const pd = new win.Event('pointerdown', { bubbles: true }); pd.pointerType = 'touch';
  stone.dispatchEvent(pd);
  stone.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
  pop = doc.querySelector('.cm-popup');
  ok(!!pop && !pop.hidden && win._htmxCalls.length === 0, 'a touch tap shows the panel only');
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  ok(!pop.isConnected || pop.hidden, 'outside click closes the card');
  // a pair served by the page uses the page's rows (core/pair_map_panel)
  cmRoot.setAttribute('data-highlight', 'pairs');   // the Pairs page from here on
  const edgeNoRow = body.querySelector('[data-cm="p:qA2-qA1"]');
  const js = doc.createElement('script');
  js.type = 'application/json'; js.id = 'cm-pair-panel';
  js.setAttribute('data-chip', 'tokA'); js.setAttribute('data-at', '12:00:00');
  js.textContent = JSON.stringify({ 'qA2-qA1': [['Best 2Q fidelity', '0.9570', 'cz_x · per gate (IRB)']] });
  doc.body.appendChild(js);
  win._pairPanelReply = null;
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  pop = doc.querySelector('.cm-popup');
  const prs = pop.querySelectorAll('.cm-popup-row');
  ok(!pop.hidden && prs.length === 1 && prs[0].querySelector('.cm-popup-v').textContent.indexOf('0.9570') === 0
     && prs[0].textContent.indexOf('per gate (IRB)') > 0, 'a pair panel shows the served rows with their kind');
  // an edit since the page loaded: the panel re-reads the pair and shows the new value
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  win._pairPanelReply = { rows: [['2Q fidelity (Chip Status)', '0.9811', '']] };
  win._fetchUrls.length = 0;
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  await tick(); await tick(); await tick();
  pop = doc.querySelector('.cm-popup');
  ok(win._fetchUrls.some((u) => u.indexOf('/api/pair-map-panel/qA2-qA1?expect_chip=tokA') === 0),
     'opening a pair panel re-reads that pair with the page chip token (' + win._fetchUrls.join(',') + ')');
  ok(pop.querySelector('.cm-popup-v').textContent.indexOf('0.9811') === 0,
     'the re-read value replaces the page copy (got ' + pop.querySelector('.cm-popup-v').textContent + ')');
  ok(!pop.querySelector('[data-panel-status]'), 'a landed re-read clears the status line');
  // an open panel follows an undo / edit: the event that patches the table re-reads it
  win._pairPanelReply = { rows: [['2Q fidelity (Chip Status)', '0.7777', 'after undo']] };
  doc.dispatchEvent(new win.CustomEvent('cellsReverted', { detail: {} }));
  await wait(500);
  ok(pop.querySelector('.cm-popup-v').textContent.indexOf('0.7777') === 0,
     'an open pair panel follows Ctrl+Z (' + pop.querySelector('.cm-popup-v').textContent + ')');
  win._pairPanelReply = { rows: [['2Q fidelity (Chip Status)', '0.9811', '']] };
  doc.dispatchEvent(new win.CustomEvent('stateRestored', { detail: {} }));
  await wait(500);
  ok(pop.querySelector('.cm-popup-v').textContent.indexOf('0.9811') === 0, 'an open pair panel follows a pull (stateRestored)');
  // an inspector commit is an htmx POST: the open panel re-reads
  win._pairPanelReply = { rows: [['2Q fidelity (Chip Status)', '0.6666', 'after commit']] };
  doc.dispatchEvent(new win.CustomEvent('htmx:afterRequest', { detail: { successful: true, requestConfig: { verb: 'post', path: '/pair/qA2-qA1/edit' } } }));
  await wait(500);
  ok(pop.querySelector('.cm-popup-v').textContent.indexOf('0.6666') === 0,
     'an open pair panel follows an inspector commit (' + pop.querySelector('.cm-popup-v').textContent + ')');
  win._pairPanelReply = { rows: [['2Q fidelity (Chip Status)', '0.9811', '']] };
  doc.dispatchEvent(new win.CustomEvent('htmx:afterRequest', { detail: { successful: true, requestConfig: { verb: 'post', path: '/pair/qA2-qA1/edit' } } }));
  await wait(500);
  // one commit fires several signals: they coalesce into ONE re-read
  const nReads = () => win._fetchUrls.filter((u) => u.indexOf('/api/pair-map-panel/') === 0).length;
  let r0 = nReads();
  const post = () => doc.dispatchEvent(new win.CustomEvent('htmx:afterRequest', { detail: { successful: true, requestConfig: { verb: 'post', path: '/pair/qA2-qA1/edit' } } }));
  post(); await wait(100); doc.dispatchEvent(new win.CustomEvent('cellsReverted', { detail: {} })); await wait(100); post();
  doc.dispatchEvent(new win.CustomEvent('quam:state-changed')); doc.dispatchEvent(new win.CustomEvent('sm:tray-swapped'));
  await wait(600);
  ok(nReads() - r0 === 1, 'one commit = one re-read (' + (nReads() - r0) + ')');
  // a GET (a page fetch, a poll) never re-reads
  r0 = nReads();
  doc.dispatchEvent(new win.CustomEvent('htmx:afterRequest', { detail: { successful: true, requestConfig: { verb: 'get' } } }));
  await wait(600);
  ok(nReads() === r0, 'a GET never re-reads');
  // the Auto-Sync push after a commit is not a second re-read
  r0 = nReads();
  doc.dispatchEvent(new win.CustomEvent('htmx:afterRequest', { detail: { successful: true, requestConfig: { verb: 'post', path: '/state/apply-to-live' } } }));
  await wait(600);
  ok(nReads() === r0, 'a push to live never re-reads');
  // the Pairs page: a qubit stone taking focus closes the open pair panel
  stone.dispatchEvent(new win.FocusEvent('focusin'));
  ok(!doc.querySelector('.cm-popup:not([hidden])') || doc.querySelector('.cm-popup:not([hidden]) .cm-popup-title').firstChild.textContent !== 'pair qA2-qA1',
     'focus on a stone closes the pair panel');
  stone.dispatchEvent(new win.FocusEvent('focusout'));
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  await tick(); await tick(); await tick();
  pop = doc.querySelector('.cm-popup');
  // a map click carries the page's chip too
  win._htmxCalls.length = 0;
  edgeNoRow.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  ok(win._htmxCalls.some((c) => c[1] === '/pair/qA2-qA1?expect_chip=tokA'),
     'a map click asks for the inspector with the page chip (' + win._htmxCalls.map((c) => c[1]).join(',') + ')');
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseleave'));
  await wait(1000);
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  await tick(); await tick(); await tick();
  pop = doc.querySelector('.cm-popup');
  // Expand carries the page's chip, so a stale window never opens another chip's inspector
  win._htmxCalls.length = 0;
  pop.querySelector('.cm-popup-hint').click();
  ok(win._htmxCalls.some((c) => c[1] === '/pair/qA2-qA1?expect_chip=tokA'),
     'Expand asks for the inspector with the page chip (' + win._htmxCalls.map((c) => c[1]).join(',') + ')');
  // an unchanged answer leaves the rows in place (a reader may be selecting in them)
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  await tick(); await tick(); await tick();
  const rowsBefore = doc.querySelector('.cm-popup .cm-popup-rows');
  doc.querySelector('.cm-popup')._rowsJson = JSON.stringify(win._pairPanelReply.rows);
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  const rowsOpen = doc.querySelector('.cm-popup .cm-popup-rows');
  await tick(); await tick(); await tick();
  ok(doc.querySelector('.cm-popup .cm-popup-rows') === rowsOpen && rowsOpen,
     'an unchanged re-read keeps the same rows element');
  const statusOf = () => { const t = doc.querySelector('.cm-popup:not([hidden]) [data-panel-status]'); return t ? t.textContent : ''; };
  const reopen = async (reply, ms) => {
    doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
    win._pairPanelReply = reply;
    edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
    await wait(ms || 5);
    return doc.querySelector('.cm-popup');
  };
  // loading
  pop = await reopen({ delay: 400, body: { rows: [['2Q fidelity (Chip Status)', '0.9811', '']] } }, 300);
  ok(/refreshing/.test(statusOf()), 'a pending re-read says refreshing (' + statusOf() + ')');
  await wait(200);
  // chip switched in another window: page copy, marked, never the other chip's numbers
  pop = await reopen({ status: 409, body: { rows: null, reason: 'chip_changed' } }, 20);
  ok(/chip changed in another window/.test(statusOf()) && pop.querySelector('.cm-popup-v').textContent.indexOf('0.98') === 0,
     'a chip change keeps the page copy and says so (' + statusOf() + ')');
  // failure: page copy with its time
  pop = await reopen({ reject: true }, 20);
  ok(/could not refresh; showing values from page load at 12:00:00/.test(statusOf()),
     'a failed re-read says so with the page-load time (' + statusOf() + ')');
  // late answer: an older open's answer neither repaints a newer panel nor overwrites newer cache
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  win._pairPanelReply = (u) => ({ delay: 120, body: { rows: [['2Q fidelity (Chip Status)', '0.1111', 'old']] } });
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  await wait(5);
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));
  win._pairPanelReply = (u) => ({ delay: 5, body: { rows: [['2Q fidelity (Chip Status)', '0.2222', 'new']] } });
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  await wait(200);
  pop = doc.querySelector('.cm-popup');
  ok(pop.querySelector('.cm-popup-v').textContent.indexOf('0.2222') === 0,
     'an older late answer does not repaint the newer panel (' + pop.querySelector('.cm-popup-v').textContent + ')');
  ok(JSON.parse(JSON.stringify(js._parsed['qA2-qA1']))[0][1] === '0.2222', 'an older answer never overwrites newer cached rows');
  win._pairPanelReply = null;
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseleave'));
  await wait(1000);
  js.remove();
  // an entity with NO matching row still gets the minimal card
  const prowEl = doc.querySelector('tr[data-pair-id="qA2-qA1"]');
  prowEl.parentNode.removeChild(prowEl);
  edgeNoRow.dispatchEvent(new win.MouseEvent('mouseenter'));
  pop = doc.querySelector('.cm-popup');
  ok(!pop.hidden && pop.querySelector('.cm-popup-title').firstChild.textContent === 'pair qA2-qA1'
     && pop.querySelectorAll('.cm-popup-row').length === 0,
     'a row-less entity gets the minimal title-only card');
  doc.body.dispatchEvent(new win.MouseEvent('click', {bubbles: true}));

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
  ok(legend && /con1\/fem1\/p1 \u00b7 2 resonators/.test(legend.textContent),
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
