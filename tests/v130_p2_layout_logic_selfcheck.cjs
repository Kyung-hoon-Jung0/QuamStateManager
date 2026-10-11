/* jsdom selfcheck for the v1.3.0 P2 layout batch: the shipped JS that decides
 * a layout, run outside a browser.  node tests/v130_p2_layout_logic_selfcheck.cjs <mode>
 *   gutter  -- L6: the Split gutter and the CSS gutter agree and hold the buttons
 *   preset  -- L27: a dataset run opens with more of the run list in view
 *   status  -- L25: "finished" is lifecycle (neutral), not success (green)
 *   context -- L27: opening a run reveals its neighbouring rows
 * Driven by tests/test_v130_p2_layout.py. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const web = path.join(__dirname, '..', 'quam_state_manager/web');
const read = file => fs.readFileSync(path.join(web, file), 'utf8');
const mode = process.argv[2];
const win = new JSDOM('<div id="table-pane"></div><div id="inspector-pane"></div>',
  { runScripts: 'outside-only' }).window;
if (mode === 'gutter') {
  const config = read('static/app.js').match(/var UI_CONFIG = \{[\s\S]*?\n\};/);
  assert.ok(config);
  win.eval(config[0]);
  const cssSize = Number(read('static/style.css').match(/--split-gutter-size:\s*(\d+)px/)[1]);
  assert.ok(win.UI_CONFIG.split.gutterSize >= 20, 'the controls fit inside the gutter');
  assert.equal(win.UI_CONFIG.split.gutterSize, cssSize, 'the CSS and Split gutter sizes agree');
} else if (mode === 'preset') {
  const config = read('static/app.js').match(/var UI_CONFIG = \{[\s\S]*?\n\};/);
  assert.ok(config);
  win.eval(config[0]);
  const split = win.UI_CONFIG.split;
  const source = read('templates/base.html');
  const match = source.match(/function expandedSizes\(\) \{[\s\S]*?\n        \}/);
  assert.ok(match, 'expandedSizes() is found in base.html');
  win.PRESET_EXPANDED_KEY = 'preset';
  let saved = null;
  win.loadPreset = (key, fallback) => saved || fallback;
  win.eval(match[0]);
  assert.deepEqual(Array.from(win.expandedSizes()), Array.from(split.expandedSizes),
    'every other inspector keeps the expanded default');
  win.document.getElementById('inspector-pane').innerHTML = '<div id="ds-detail-root"></div>';
  const ds = Array.from(win.expandedSizes());
  assert.deepEqual(ds, Array.from(split.datasetExpandedSizes), 'a dataset run uses its own default');
  assert.ok(ds[0] >= 25 && ds[0] + ds[1] === 100, 'the run list keeps at least a quarter of the pane');
  assert.ok(split.expandedSizes[0] < ds[0], 'and more than the 15% sliver');
  saved = [0, 100];
  assert.deepEqual(Array.from(win.expandedSizes()), saved, 'an explicit user preset wins');
} else if (mode === 'status') {
  const match = read('static/dataset-virtual.js').match(/function statusChip\(status\) \{[\s\S]*?\n    \}/);
  assert.ok(match);
  win.escapeHtml = value => { const e = win.document.createElement('span'); e.textContent = value; return e.innerHTML; };
  win.eval(match[0]);
  for (const status of ['finished', 'done', 'completed']) {
    const e = win.document.createElement('div'); e.innerHTML = win.statusChip(status);
    assert.ok(e.firstElementChild.classList.contains('ds-status-done'), 'lifecycle completion is neutral');
    assert.equal(e.textContent, status);
  }
  assert.match(win.statusChip('success'), /ds-status-ok/);
  assert.match(win.statusChip('failed'), /ds-status-err/);
  assert.match(win.statusChip('running'), /ds-status-run/);
} else if (mode === 'context') {
  const source = read('static/dataset-virtual.js');
  const fn = source.match(/function _kbReveal\([^)]*\) \{[\s\S]*?\n    \}/);
  assert.ok(fn);
  const pane = win.document.getElementById('table-pane');
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 212 });
  win.state = { scrollEl: pane, tbody: {
    getBoundingClientRect: () => ({ top: 800 - pane.scrollTop }),
    parentNode: { querySelector: () => ({ getBoundingClientRect: () => ({ height: 28 }) }) }
  } };
  pane.getBoundingClientRect = () => ({ top: 0 });
  win._kbIdx = 1; win.ROW_HEIGHT = 32;
  win.renderWindow = () => {}; win._kbHighlight = () => {};
  win.eval(fn[0]);
  win._kbReveal(true);
  assert.equal(pane.scrollTop, 772, 'opening a run leaves room for neighboring rows');
  pane.scrollTop = 0; win._kbReveal();
  assert.equal(pane.scrollTop, 652, 'ordinary keyboard moves retain nearest-row scrolling');
  Object.defineProperty(pane, 'clientHeight', { configurable: true, value: 50 });
  pane.scrollTop = 0; win._kbReveal(true);
  assert.equal(pane.scrollTop, 814, 'a small user preset still reveals the selected row');
  const sync = source.match(/function _kbSyncTo\([^)]*\) \{[\s\S]*?\n    \}/);
  assert.ok(sync);
  let context;
  win._kbBound = () => true;
  win._kbReveal = value => { context = value; };
  win.state.visible = [0]; win.state.rows = [{ uid: 'run' }];
  win.eval(sync[0]); win._kbSyncTo('run', true);
  assert.equal(context, true, 'row synchronization forwards the requested context');
  const handler = source.slice(source.indexOf('if (!window._dsKbBound)')).match(
    /document.addEventListener\('htmx:afterSwap', function \(e\) \{[\s\S]*?\n        \}\);/);
  assert.ok(handler);
  win._kbSyncTo = (uid, value) => { assert.equal(uid, 'run'); context = value; };
  win.document.getElementById('inspector-pane').innerHTML = '<div id="ds-detail-root" data-uid="run"></div>';
  win.eval(handler[0]); context = null;
  win.document.dispatchEvent(new win.CustomEvent('htmx:afterSwap', {
    detail: { target: win.document.getElementById('inspector-pane') }
  }));
  assert.equal(context, true, 'opening the inspector requests neighboring rows');
} else {
  assert.fail('unknown layout check');
}
console.log('v130_p2_layout_logic_selfcheck: ' + mode + ' assertions passed');
