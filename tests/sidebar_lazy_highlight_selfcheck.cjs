/* R4-DS-14: syncSidebarTreeHighlight reaches a run inside a COLLAPSED lazy
 * date group (docs/142 ships it empty): it opens that group, loads its runs
 * with the group's own declared request, and lands the highlight on the swap.
 * Runs the real function source from app.js under jsdom with an htmx stub.
 * Driven by tests/test_sidebar_lazy_highlight.py. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const app = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8').split(String.fromCharCode(13)).join('');
function grab(name) {
    const i = app.indexOf('function ' + name + '(');
    assert.ok(i >= 0, name + ' is in app.js');
    const j = app.indexOf('\n}\n', i);
    return app.slice(i, j + 3);
}
const dom = new JSDOM(`<!doctype html><body><div id="sidebar-tree">
  <details class="tree-dir tree-date" data-lazy-group="1" hx-get="/workspace/tree/group?capped=1"
           hx-vals='{"root": "R", "tpath": "2026-10-30"}'>
    <summary class="tree-date-label">2026-10-30 <small>(3)</small></summary>
    <ul class="tree-entries" data-lazy="1"><li class="tree-entry muted tree-lazy-hint"><small>loading</small></li></ul>
  </details></div></body>`, { runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const asked = [];
w.htmx = { ajax(method, url, opts) { asked.push({ method, url, opts }); } };
w._markActiveTreeBranch = () => {};
w.Element.prototype.scrollIntoView = function () {};
w.eval('var _pendingTreeHighlight = null;\n' + grab('_openTreeAncestors') + grab('syncSidebarTreeHighlight')
       + 'window.__pending = function () { return _pendingTreeHighlight; };'
       + 'window.syncSidebarTreeHighlight = syncSidebarTreeHighlight;');
const g = d.querySelector('details');
w.syncSidebarTreeHighlight('run-7', '2026-10-30');
assert.equal(g.open, true, 'the collapsed lazy group of that date is opened');
assert.equal(asked.length, 1, 'its runs are asked for once');
assert.equal(asked[0].url, '/workspace/tree/group?capped=1', "with the group's own declared request");
assert.deepEqual(JSON.parse(JSON.stringify(asked[0].opts.values)), { root: 'R', tpath: '2026-10-30' });
assert.equal(asked[0].opts.target, g.querySelector('ul.tree-entries'));
assert.equal(w.__pending().uid, 'run-7', 'the highlight waits for the swap');
// an unrelated swap lands first: the retry must not ask a second time
const p1 = w.__pending(); w.eval('_pendingTreeHighlight = null;');
w.syncSidebarTreeHighlight(p1.uid, p1.date);
assert.equal(asked.length, 1, 'a retry while loading does not ask again');
// the runs arrive; the retry finds the row
g.querySelector('ul').innerHTML = '<li><span class="tree-entry-click" data-uid="run-7">#7</span></li>';
const p2 = w.__pending(); w.eval('_pendingTreeHighlight = null;');
w.syncSidebarTreeHighlight(p2.uid, p2.date);
assert.ok(d.querySelector('[data-uid="run-7"]').classList.contains('tree-entry-active'), 'the run is highlighted');
// a date with no group asks nothing
w.syncSidebarTreeHighlight('run-9', '1999-01-01');
assert.equal(asked.length, 1, 'no group of that date: nothing is asked');
console.log('sidebar_lazy_highlight_selfcheck ok');
