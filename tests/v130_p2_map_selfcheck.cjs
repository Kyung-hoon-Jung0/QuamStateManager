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
  { id: 'q1', grid_location: '0,0', f_01: 5e9 },
  { id: 'q2', grid_location: '1,0', f_01: 5.2e9 },
];
const edges = [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', moving_qubit: 'control' }];
for (const highlight of ['pairs', 'flux', 'resonators']) {
  win.TopoGraph.renderLayout(mount, { nodes, edges, highlight });
  const edge = mount.querySelector('.cm-edge');
  const role = mount.querySelector('.cm-role');
  assert.ok(edge && role, 'pair line and role labels are drawn');
  assert.ok(edge.compareDocumentPosition(role) & win.Node.DOCUMENT_POSITION_FOLLOWING,
    'role labels are painted after the pair lines');
  for (const node of mount.querySelectorAll('.cm-node')) {
    const background = node.querySelector('.cm-occluder');
    const stone = node.querySelector('.cm-stone');
    const label = node.querySelector('.cm-id');
    assert.ok(background, 'dimmed stones must mask the lines underneath');
    assert.equal(background.getAttribute('r'), stone.getAttribute('r'));
    assert.ok(edge.compareDocumentPosition(background) & win.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.ok(background.compareDocumentPosition(label) & win.Node.DOCUMENT_POSITION_FOLLOWING,
      'the opaque background is below the ID label');
  }
}
console.log('v130_p2_map_selfcheck: 30 assertions passed');
