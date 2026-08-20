// docs/120 item 11 — the qubit detail popup SURVIVES the card diagram's
// deletion, and the hero grew the pair information the cards used to carry.
//
// This file exists because of one specific trap. `_sharedQubitPopup` is what
// the hero's hover handler opens, and it was only ever ASSIGNED inside
// buildTopology — the IIFE that rendered the card diagram. Deleting that block
// wholesale would have left the bridge null forever, and `bindHover` simply
// guards on it:
//
//     if (!_sharedQubitPopup) return;
//
// so the hover popup would have stopped opening with NO error, NO console
// warning and NO other failing test. A regression nothing announces is exactly
// the kind that needs its own pin. The machinery now lives in its own
// `buildQubitPopup`, owning nothing but itself.
//
// Async because the popup opens after a ~260ms hover-intent delay, which is
// why it could not be asserted inside the synchronous hero selfcheck.
//
// Run: node tests/hero_popup_selfcheck.cjs   (needs jsdom)
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
const read = (f) => fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', f), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const topo = {
  nodes: [
    { id: 'qA1', grid_location: '0,0', f_01: 6.10e9, T1: 2.4e-5,
      metrics: { T1: { value: 2.4e-5 } } },
    { id: 'qA2', grid_location: '1,0', f_01: 5.80e9, T1: 1.9e-5,
      metrics: { T1: { value: 1.9e-5 } } },
    { id: 'qA3', grid_location: '2,0', f_01: 6.40e9, T1: 2.1e-5,
      metrics: { T1: { value: 2.1e-5 } } },
  ],
  edges: [
    // control=qA1, target=qA2, and the TARGET is what moves
    { pair_id: 'qA1-2', source: 'qA1', target: 'qA2', has_cz: true,
      cz_fidelity: 0.99, moving_qubit: 'target', metrics: {} },
    // control=qA3, target=qA2, and the CONTROL moves — the other branch
    { pair_id: 'qA3-2', source: 'qA3', target: 'qA2', has_cz: true,
      cz_fidelity: null, moving_qubit: 'control', metrics: {} },
  ],
  summary: {},
};

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="topo-hero"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(win);
  win.ChipStatus.mount({ topo: topo, rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {} });
  return win;
}

const win = makeWorld();
const doc = win.document;
const hero = doc.getElementById('topo-hero');

/* ── A. one map, and it is the hero ──────────────────────────────────── */
ok(!!hero.querySelector('svg'), 'A1: the hero map renders');
ok(doc.getElementById('topo-html-wrap') === null, 'A2: no card-diagram host');
ok(doc.querySelectorAll('.topo-node-card').length === 0, 'A3: no property cards');

/* ── B. the pair information the cards carried is on the hero now ────── */
ok(hero.querySelectorAll('.cm-freq').length === 2,
  'B1: a frequency chevron per pair (shared TopoGraph.pairGlyphs)');
ok(hero.querySelectorAll('.cm-role-c').length === 2, 'B2: a C marker per pair');
ok(hero.querySelectorAll('.cm-role-t').length === 2, 'B3: a T marker per pair');
ok(hero.querySelectorAll('.cm-role-m').length === 2, 'B4: an M marker per pair');

/* M is a ROLE, so it coincides with C or T — never a third position. It must
   sit next to the end that actually moves, which differs per pair here. */
function markerXY(sel, nth) {
  const c = hero.querySelectorAll(sel)[nth].querySelector('circle');
  return { x: parseFloat(c.getAttribute('cx')), y: parseFloat(c.getAttribute('cy')) };
}
function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
{
  // pair 0: moving = TARGET  -> M is nearer the T marker than the C marker
  const c0 = markerXY('.cm-role-c', 0), t0 = markerXY('.cm-role-t', 0),
        m0 = markerXY('.cm-role-m', 0);
  ok(dist(m0, t0) < dist(m0, c0),
    'B5: moving_qubit="target" puts M at the TARGET end');
  // pair 1: moving = CONTROL -> the other way round
  const c1 = markerXY('.cm-role-c', 1), t1 = markerXY('.cm-role-t', 1),
        m1 = markerXY('.cm-role-m', 1);
  ok(dist(m1, c1) < dist(m1, t1),
    'B6: moving_qubit="control" puts M at the CONTROL end');
}

/* No role recorded => no M invented. quam_builder defaults the mover to the
   higher-f_01 qubit, so guessing from the frequencies would hide exactly the
   override worth seeing. */
{
  const bare = JSON.parse(JSON.stringify(topo));
  bare.edges.forEach(function (e) { e.moving_qubit = null; });
  const w2 = (function () {
    const d = new JSDOM('<!DOCTYPE html><html><body><div id="topo-hero"></div></body></html>',
      { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const w = d.window;
    w.htmx = { ajax: function () {} };
    w.fetch = function () { return new w.Promise(function () {}); };
    new w.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(w);
    w.ChipStatus.mount({ topo: bare, rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {} });
    return w;
  })();
  const h2 = w2.document.getElementById('topo-hero');
  ok(h2.querySelectorAll('.cm-role-m').length === 0,
    'B7: no moving_qubit recorded => no M is invented from the frequencies');
  ok(h2.querySelectorAll('.cm-role-c').length === 2,
    'B8: ...but C/T still render — the roles are known');
}

/* ── C. THE POINT OF THIS FILE: the popup still opens ─────────────────── */
const q = hero.querySelector('[data-hero-qubit="qA1"]');
ok(!!q, 'C0: the hero node is there to hover');
q.dispatchEvent(new win.MouseEvent('mouseenter', { bubbles: true }));

setTimeout(function () {
  const pop = win.document.querySelector('.topo-card-popup');
  ok(!!pop,
    'C1: hovering a hero qubit STILL opens the detail popup after the cards '
    + 'were deleted (the silent-regression trap this file exists for)');
  if (pop) {
    ok(/qA1/.test(pop.textContent),
      'C2: and it is the hovered qubit\'s popup');
  }
  process.exit(fails ? 1 : 0);
}, 700);
