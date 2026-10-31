/* jsdom selfcheck for v1.3.0 P2 L2: on the Pairs / Flux / Resonators component
 * maps nothing is painted over a qubit id or a direction mark (D-94).
 *  - the pair lines run under every stone: an opaque disc (cm-occluder) under
 *    each stone hides them even where the page dims the stone itself;
 *  - the direction marks (arrow, moving ring) sit outside every stone.
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
  // D-94: direction is the edge's own arrow + moving ring (no C/T/M letters)
  const arrow = mount.querySelector('.cm-edge .cm-dir-arrow'), ring = mount.querySelector('.cm-edge .cm-dir-m');
  assert.ok(edge && arrow && ring, highlight + ': pair line, its arrow and the moving ring are drawn'); n++;
  assert.equal(mount.querySelectorAll('.cm-role').length, 0, highlight + ': no C/T/M role letters'); n++;
  const marks = mount.querySelectorAll('.cm-node .cm-flux, .cm-node .cm-res');
  assert.ok(marks.length === 4, highlight + ': both stones carry a flux stub and a resonator mark'); n++;
  // the arrow tip and the ring sit OUTSIDE each stone, so the stones painted
  // after the edges never cover them
  const stones = Array.from(mount.querySelectorAll('.cm-stone')).map((c) => [+c.getAttribute('cx'), +c.getAttribute('cy'), +c.getAttribute('r')]);
  const tip = arrow.getAttribute('points').split(' ')[2].split(',').map(Number);
  const rc = [+ring.getAttribute('cx'), +ring.getAttribute('cy'), +ring.getAttribute('r')];
  for (const st of stones) {
    assert.ok(Math.hypot(tip[0] - st[0], tip[1] - st[1]) > st[2], highlight + ': the arrow tip is outside every stone'); n++;
    assert.ok(Math.hypot(rc[0] - st[0], rc[1] - st[1]) > st[2] + rc[2], highlight + ': the moving ring is outside every stone'); n++;
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
