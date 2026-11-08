// jsdom selfcheck: a Json-tree row follows a change of its value's KIND
// (FB-07). An Edit-as-JSON save of a null leaf landed (working copy + live)
// while the row kept reading null: the follow-up patch rewrote the text but
// not the leaf's display baseline, so the next hover put "null" back. An undo
// of a list / dict back to null never repainted at all (a container row has
// no .tree-val). Every caller now goes through ONE repaint, _treeRepaintPath.
//
// Run: node tests/tree_kind_change_selfcheck.cjs   (driven by
// tests/test_undo_trail.py::test_tree_kind_change_selfcheck)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const APP_JS = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 15); }); }
function jsonResp(obj) {
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(obj); } });
}
const DATA = { port: { lo_mode: null, band: 2, ff: [1, 0.5], ct: null } };

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>'
    + '<div id="explorer-tree-state" class="json-tree"></div><div id="pending-tray"></div>'
    + '</body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/explorer' });
  const win = dom.window;
  win._server = JSON.parse(JSON.stringify({ 'port.lo_mode': null, 'port.band': 2, 'port.ff': [1, 0.5], 'port.ct': null }));
  win.fetch = function (url) {
    url = String(url);
    if (url.indexOf('/field/peek') === 0) {
      const dp = decodeURIComponent(url.split('dot_path=')[1] || '');
      const values = {}; values[dp] = win._server[dp];
      return jsonResp({ ok: true, values: values, errors: {}, expected: {} });
    }
    if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
    return new Promise(function () {});
  };
  win.htmx = { ajax: function () { return Promise.resolve(); }, trigger: function () {}, process: function () {} };
  new win.Function(APP_JS).call(win);
  win.renderJsonTree('explorer-tree-state', JSON.parse(JSON.stringify(DATA)), { defaultDepth: 3, crud: true });
  return win;
}
function node(win, p) { return win.document.querySelector('#explorer-tree-state .tree-node[data-path="' + p + '"]'); }
function shown(win, p) {
  const n = node(win, p); if (!n) return 'NO-NODE';
  const v = n.querySelector(':scope > .tree-row > .tree-val');
  if (!v) return 'CONTAINER';
  // what a hover / editor unmount puts back is the display baseline
  return v._treeDisplay !== undefined ? v._treeDisplay : v.textContent;
}
function pencil(win, p) { const n = node(win, p); return !!(n && n.querySelector(':scope > .tree-row > .tree-json-edit-btn')); }

(async function () {
  // 1. the live patch (Edit-as-JSON save -> /explorer/model -> LiveSurfacePatch)
  let w = makeWorld();
  ok(shown(w, 'port.lo_mode') === 'null' && pencil(w, 'port.lo_mode'), 'start: a null leaf with its JSON pencil');
  w.LiveSurfacePatch.apply([{ dot_path: 'port.lo_mode', value: 'always_on', old_value_str: 'always_on' }]);
  ok(shown(w, 'port.lo_mode') === '"always_on"', 'patch null -> string: the display baseline reads the string (' + shown(w, 'port.lo_mode') + ')');
  const v = node(w, 'port.lo_mode').querySelector(':scope > .tree-row > .tree-val');
  ok(v.classList.contains('tree-val-string') && !v.classList.contains('tree-val-null'), 'patch null -> string: kind class follows');
  ok(!pencil(w, 'port.lo_mode'), 'patch null -> string: the null-only pencil is gone');
  ok(node(w, 'port.lo_mode')._value === 'always_on', 'patch null -> string: the node value follows');
  w.LiveSurfacePatch.apply([{ dot_path: 'port.band', value: null, old_value_str: 'null' }]);
  ok(shown(w, 'port.band') === 'null' && pencil(w, 'port.band'), 'patch number -> null: reads null and offers the JSON pencil');
  w.LiveSurfacePatch.apply([{ dot_path: 'port.ct', value: { a: 1 }, old_value_str: '{"a":1}' }]);
  ok(shown(w, 'port.ct') === 'CONTAINER' && /1 key/.test(node(w, 'port.ct').querySelector(':scope > .tree-row').textContent), 'patch null -> dict: the row becomes a container with its key');

  // 2. _paintTreeLeaf (a batch's other rows)
  w = makeWorld();
  w.LiveSurfacePatch.apply([{ dot_path: 'port.ff', value: null, old_value_str: 'null' }]);
  ok(shown(w, 'port.ff') === 'null' && !node(w, 'port.ff.0'), 'patch list -> null: the children are gone and the row reads null');

  // 3. undo / redo revert (peek-driven) across kinds
  w = makeWorld();
  w._server['port.lo_mode'] = 'always_on';
  w._revertTreeNode('port.lo_mode', 'always_on'); await tick(30);
  ok(shown(w, 'port.lo_mode') === '"always_on"' && !pencil(w, 'port.lo_mode'), 'revert null -> string repaints (' + shown(w, 'port.lo_mode') + ')');
  w._server['port.lo_mode'] = null;
  w._revertTreeNode('port.lo_mode', 'null'); await tick(30);
  ok(shown(w, 'port.lo_mode') === 'null' && pencil(w, 'port.lo_mode'), 'revert string -> null repaints with the pencil back');
  w._server['port.ff'] = null;
  w._revertTreeNode('port.ff', 'null'); await tick(30);
  ok(shown(w, 'port.ff') === 'null', 'revert list -> null rebuilds the container row as a null leaf (' + shown(w, 'port.ff') + ')');
  w._server['port.ff'] = [3, 4];
  w._revertTreeNode('port.ff', '[3,4]'); await tick(30);
  ok(shown(w, 'port.ff') === 'CONTAINER' && /2 item/.test(node(w, 'port.ff').querySelector(':scope > .tree-row').textContent), 'revert null -> list rebuilds a container with its items');
  w._server['port.band'] = 7;
  w._revertTreeNode('port.band', '7'); await tick(30);
  ok(shown(w, 'port.band') === '7' && node(w, 'port.band')._value === 7, 'a plain number -> number revert paints in place and moves the node value');

  console.log(fails ? fails + ' FAILED' : 'all tree kind-change checks passed');
  process.exit(fails ? 1 : 0);
})();
