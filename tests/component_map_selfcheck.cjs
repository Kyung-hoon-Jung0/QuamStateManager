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
    { id: 'qA1', grid_location: '0,0', chain: 'A', rr_port: 'con1/fem1/p1', z_port: 'con1/fem5/p1' },
    { id: 'qA2', grid_location: '1,0', chain: 'A', rr_port: 'con1/fem1/p1', z_port: 'con1/fem5/p2' },
    { id: 'qA3', grid_location: '0,1', chain: 'B', rr_port: 'con1/fem2/p1', z_port: null },
  ],
  edges: [
    { pair_id: 'qA2-qA1', source: 'qA2', target: 'qA1', directed: false, has_coupler: true, active: null },
    { pair_id: 'qA1-qA3', source: 'qA1', target: 'qA3', directed: true, has_coupler: false, active: true },
  ],
};

function makeWorld(bodyHtml) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + bodyHtml + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._htmxCalls = [];
  win.htmx = { ajax: function () { win._htmxCalls.push(Array.prototype.slice.call(arguments)); } };
  win.fetch = function () {
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
  ok(m.querySelectorAll('.cm-arrow').length === 1, 'CR direction arrow drawn');
  ok(m.querySelectorAll('.cm-coupler').length === 1, 'coupler dot drawn on the has_coupler edge');
  ok(m.querySelectorAll('.cm-res').length === 3, 'resonator marks for all 3 (all have rr_port)');
  ok(m.querySelectorAll('.cm-flux').length === 2, 'flux stubs only for qubits WITH a z_port');
  const feeds = m.querySelectorAll('.cm-feed');
  ok(feeds.length === 1, 'ONE feedline bus for the shared rr_port group (con1/fem1/p1 x2)');

  // §2.4: NO numbers — the only rendered text is the qubit id
  const texts = m.querySelectorAll('svg text');
  ok(texts.length === 3, 'exactly one text per qubit (no value text anywhere)');
  const idSet = { qA1: 1, qA2: 1, qA3: 1 };
  let onlyIds = true;
  texts.forEach(function (t) {
    if (!idSet[t.textContent] || t.getAttribute('class') !== 'cm-id') onlyIds = false;
  });
  ok(onlyIds, 'every rendered text is a bare qubit id (numbers live in the table — docs/91 §2.4)');

  // emphasis never changes content: same drawing under a different highlight
  const m2 = win.document.createElement('div');
  win.document.body.appendChild(m2);
  win.TopoGraph.renderLayout(m2, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: 'resonators' });
  const strip = (h) => h.replace(/cm-hl-[a-z]+/g, 'cm-hl-X');
  ok(strip(m.innerHTML) === strip(m2.innerHTML),
     'highlight changes EMPHASIS only — drawings byte-identical up to the mode class');

  // F2 (docs/93): text scales with the cell — default cell keeps the exact
  // legacy 10.5px; a 120px cell scales geometry AND font together.
  const idEl = m.querySelector('.cm-id');
  ok(idEl.getAttribute('font-size') === '10.5', 'default cell renders the legacy 10.5px id font');
  const mBig = win.document.createElement('div');
  win.document.body.appendChild(mBig);
  win.TopoGraph.renderLayout(mBig, { nodes: TOPO.nodes, edges: TOPO.edges, highlight: 'pairs', cell: 120 });
  const bigSvg = mBig.querySelector('svg');
  ok(parseInt(bigSvg.getAttribute('width'), 10) === 240,
     'cell:120 doubles the drawing width (2 cols -> 240, got ' + bigSvg.getAttribute('width') + ')');
  ok(mBig.querySelector('.cm-stone').getAttribute('r') === '36', 'cell:120 -> 36px stones (0.30 ratio kept)');
  ok(mBig.querySelector('.cm-id').getAttribute('font-size') === '19.7',
     'cell:120 -> 19.7px id font (anchored to 10.5@64)');

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
  + '<table><tbody>'
  + '<tr data-pair-id="qA2-qA1"><td>pair row</td></tr>'
  + '<tr data-qubit-id="qA1"><td>qubit row</td></tr>'
  + '</tbody></table>'
  + '</div><div id="inspector-pane"></div>';

async function checkComponentMap() {
  const win = makeWorld(PANE_HTML);
  const doc = win.document;
  win.ComponentMap.mount(doc.getElementById('component-map'));
  await tick(); await tick();

  const body = doc.querySelector('.cmap-body');
  ok(!!body.querySelector('svg.cm-svg'), 'mount fetches /api/topology and renders the layout');

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

  // HTMX swap: replace the pane content, re-mount — binding still single
  const pane = doc.getElementById('table-pane');
  pane.innerHTML = PANE_HTML.replace('<div id="table-pane">', '').replace('</div><div id="inspector-pane"></div>', '');
  win.ComponentMap.mount(doc.getElementById('component-map'));
  await tick(); await tick();
  const body2 = pane.querySelector('.cmap-body');
  ok(!!body2.querySelector('svg.cm-svg'), 're-mount after a swap renders again');
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
  root2.setAttribute('open', 'open');
  root2.dispatchEvent(new win2.Event('toggle'));
  await tick(); await tick();
  ok(fetches === 1, 'opening it loads exactly once');
  ok(win2.localStorage.getItem('quam_component_map_open') === '1', 'the choice persists');

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
