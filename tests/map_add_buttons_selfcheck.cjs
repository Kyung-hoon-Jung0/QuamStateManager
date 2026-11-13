/* The component map's two add buttons (FB-10 A/B) against the REAL
 * topo-graph.js + component-map.js + diagram-add.js under jsdom.
 *
 * Pins:
 *  - hovering or focusing an add button offers NO Expand (they are controls,
 *    not entities), while a qubit stone still offers one;
 *  - both buttons read two lines, "Add" over "qubit" / "pair", at one size;
 *  - aria-labels are "Add qubit" / "Add pair".
 *
 * Run: node tests/map_add_buttons_selfcheck.cjs   (driven by tests/test_map_add_buttons.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const read = (f) => fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', f), 'utf8');
const SRC = read('topo-graph.js') + '\n;\n' + read('component-map.js') + '\n;\n' + read('diagram-add.js');
let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }
const TOPO = {
  nodes: [{ id: 'q1', grid_location: '0,0' }, { id: 'q2', grid_location: '1,0' }],
  edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2' }],
};
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane"><details class="cmap" id="component-map" ' +
  'data-highlight="qubits" data-cell="120" data-cm-add="1" open><summary>m</summary><div class="cmap-body"></div></details>' +
  '<div id="cm-add-panel"></div><div id="inspector-pane"></div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
win.htmx = { ajax: () => win.Promise.resolve() };
win.fetch = (url) => win.Promise.resolve({ ok: true, json: () => win.Promise.resolve(JSON.parse(JSON.stringify(TOPO))) });
new win.Function(SRC).call(win);
win.ComponentMap.mount(win.document.getElementById('component-map'));
const tick = () => new Promise((r) => setTimeout(r, 0));
(async function () {
  await tick(); await tick();
  const doc = win.document;
  // Expand is either the shared explicit button or, on the Qubits/Pairs hover
  // panel (0a773867), the panel's own Expand button.
  const expand = () => doc.querySelector('.topo-expand') ||
    Array.prototype.find.call(doc.querySelectorAll('.cm-popup-hint'),
      (b) => b.textContent === 'Expand' && !(b.closest('[hidden]')));
  const hover = (el) => el.dispatchEvent(new win.MouseEvent('mouseenter', { bubbles: false }));
  const ghosts = doc.querySelectorAll('.cm-dir-ghost');
  ok(ghosts.length === 2, 'two add buttons: ' + ghosts.length);
  ghosts.forEach((g) => {
    hover(g);
    ok(!expand(), 'no Expand when hovering ' + g.getAttribute('data-cm'));
    g.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
    ok(!expand(), 'no Expand when focusing ' + g.getAttribute('data-cm'));
  });
  hover(doc.querySelector('[data-cm="q:q1"]'));
  ok(!!expand(), 'a qubit stone still offers Expand');
  ok(ghosts[0].textContent === '+ Add qubit' && ghosts[1].textContent === '+ Add pair',
     'button labels: ' + ghosts[0].textContent + ' / ' + ghosts[1].textContent);
  ok(ghosts[0].className === ghosts[1].className, 'both buttons share one style');
  ok(ghosts[0].tagName === 'BUTTON' && ghosts[1].tagName === 'BUTTON', 'native, keyboard-reachable buttons');
  ok(!doc.querySelector('svg.cm-svg .cm-dir-ghost'), 'the add buttons sit outside the map drawing');
  console.log((fails ? 'FAILED ' : 'ok ') + (n - fails) + '/' + n);
  process.exit(fails ? 1 : 0);
})();
