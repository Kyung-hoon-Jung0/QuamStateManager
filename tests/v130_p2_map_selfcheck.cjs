/* jsdom selfcheck for v1.3.0 P2 L2: on the Pairs / Flux / Resonators component
 * maps nothing is painted over a qubit id or a C/T/M role letter.
 *  - the pair lines run under every stone: an opaque disc (cm-occluder) under
 *    each stone hides them even where the page dims the stone itself;
 *  - the role markers come after the stones' groups, so a flux stub or a
 *    resonator mark (part of a stone's group) never covers a letter.
 * Run: node tests/v130_p2_map_selfcheck.cjs   (driven by tests/test_v130_p2_layout.py)
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="map"></div>', { runScripts: 'outside-only' });
const win = dom.window;
win.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/topo-graph.js'), 'utf8'));
const mount = win.document.getElementById('map');
const nodes = [
  { id: 'q1', grid_location: '0,0', f_01: 5e9, z_port: 'con1/fem1/p1', rr_port: 'con1/fem2/p1' },
  { id: 'q2', grid_location: '1,0', f_01: 5.2e9, z_port: 'con1/fem1/p2', rr_port: 'con1/fem2/p1' },
];
const edges = [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', moving_qubit: 'control' }];
const after = (a, b) => !!(a.compareDocumentPosition(b) & win.Node.DOCUMENT_POSITION_FOLLOWING);
let n = 0;
for (const highlight of ['pairs', 'flux', 'resonators']) {
  win.TopoGraph.renderLayout(mount, { nodes, edges, highlight });
  const edge = mount.querySelector('.cm-edge');
  const roles = mount.querySelectorAll('.cm-role');
  assert.ok(edge && roles.length >= 3, highlight + ': pair line and C/T/M markers are drawn'); n++;
  const marks = mount.querySelectorAll('.cm-node .cm-flux, .cm-node .cm-res');
  assert.ok(marks.length === 4, highlight + ': both stones carry a flux stub and a resonator mark'); n++;
  for (const role of roles) {
    assert.ok(after(edge, role), highlight + ': role letters are painted after the pair lines'); n++;
    for (const mark of marks) {
      assert.ok(after(mark, role), highlight + ': role letters are painted after the flux/resonator marks'); n++;
    }
  }
  for (const node of mount.querySelectorAll('.cm-node')) {
    const disc = node.querySelector('.cm-occluder');
    const stone = node.querySelector('.cm-stone');
    const label = node.querySelector('.cm-id');
    assert.ok(disc, highlight + ': a dimmed stone still masks the lines underneath'); n++;
    assert.equal(disc.getAttribute('r'), stone.getAttribute('r')); n++;
    assert.ok(after(edge, disc), highlight + ': the disc is painted after the pair line'); n++;
    assert.ok(after(disc, stone) && after(disc, label), highlight + ': and under the stone and its id'); n++;
  }
}
console.log('v130_p2_map_selfcheck: ' + n + ' assertions passed');
