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
  const source = read('templates/base.html');
  const match = source.match(/function expandedSizes\(\) \{(?:[^\n]+?\}|[\s\S]*?\n        \})/);
  assert.ok(match, 'dataset and component presets are chosen separately');
  win.UI_CONFIG = { split: { expandedSizes: [15, 85] } };
  win.PRESET_EXPANDED_KEY = 'preset';
  let saved = null;
  win.loadPreset = (key, fallback) => saved || fallback;
  win.eval(match[0]);
  assert.deepEqual(Array.from(win.expandedSizes()), [15, 85], 'qubit inspector keeps its preset');
  win.document.getElementById('inspector-pane').innerHTML = '<div id="ds-detail-root"></div>';
  assert.deepEqual(Array.from(win.expandedSizes()), [25, 75], 'run list retains useful context');
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
} else if (mode === 'grid') {
  const source = read('static/bulk-edit.js');
  const scroller = source.match(/function _scrollerOf\(t\) \{[\s\S]*?\n    \}/);
  assert.ok(scroller);
  win.eval(scroller[0]);
  const pane = win.document.getElementById('table-pane');
  pane.innerHTML = '<div class="bulk-table-wrap"><table id="bulk-table"></table></div>';
  const wrap = pane.firstElementChild;
  assert.equal(win._scrollerOf(wrap.firstElementChild), wrap, 'the grid owns horizontal scrolling');
  const pair = read('static/pair-edit.js').match(/scroller: function \(t\) \{([\s\S]*?)\n            \}/);
  assert.ok(pair);
  win.eval('window.pairScroller = function(t) {' + pair[1] + '}');
  assert.equal(win.pairScroller(wrap.firstElementChild), wrap, 'pair grids own horizontal scrolling too');
  const noteFn = read('static/grid-virt.js').match(/function note\(msg\) \{[\s\S]*?\n        \}/);
  assert.ok(noteFn);
  const note = win.document.createElement('p'); pane.insertBefore(note, wrap);
  win.table = () => wrap.querySelector('table');
  win.noteEl = () => note; win.scrollerOf = win._scrollerOf;
  win.eval(noteFn[0]); wrap.scrollLeft = 1200; win.note('Could not load');
  assert.equal(note.style.transform, '', 'a note beside the grid stays in place');
  wrap.appendChild(note); win.note('Could not load');
  assert.equal(note.style.transform, 'translateX(1200px)', 'notes inside a scroll frame remain pinned');
  pane.insertBefore(note, wrap);
  const start = source.indexOf('    function _pinGridHeadsToPane()');
  assert.ok(start >= 0, 'vertical header tracking is mounted');
  const end = source.indexOf('    function _pinBarsToScroll()', start);
  win.eval(source.slice(start, end));
  pane.getBoundingClientRect = () => ({ top: 100 });
  let origin = 350;
  wrap.getBoundingClientRect = () => ({ top: origin - pane.scrollTop });
  let resized;
  win.ResizeObserver = class { constructor(cb) { resized = cb; } observe() {} disconnect() {} };
  win._pinGridHeadsToPane();
  assert.equal(wrap.style.getPropertyValue('--bulk-sticky-top'), '0px');
  pane.scrollTop = 300; pane.dispatchEvent(new win.Event('scroll'));
  assert.equal(wrap.style.getPropertyValue('--bulk-sticky-top'), '50px', 'header follows vertical pane scrolling');
  origin = 400; resized();
  assert.equal(wrap.style.getPropertyValue('--bulk-sticky-top'), '0px', 'layout changes update the header origin');
  assert.match(source.slice(end, end + 120), /_pinGridHeadsToPane\(\)/, 'normal mounting arms header tracking');
  const bars = source.slice(end).match(/function _pinBarsToScroll\(\) \{[\s\S]*?\n    \}/);
  assert.ok(bars);
  win._pinBars = scroller => { assert.equal(scroller, pane, 'toolbar rows follow the pane'); };
  win.eval(bars[0]); win._pinBarsToScroll();
} else {
  assert.fail('unknown layout check');
}
console.log('v130_p2_layout_logic_selfcheck: ' + mode + ' assertions passed');
