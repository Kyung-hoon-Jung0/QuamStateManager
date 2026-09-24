// jsdom selfcheck: the Json tree after a STRUCTURAL undo / discard, the null
// leaf's JSON pencil, and Enter in the add-key panel (QA pass 2026-09-24).
//  - JT-04: undoing (or discarding) an added key left a phantom '<key>: null'
//    row -- the peek says the key is gone, the tree painted null and wrote
//    key=null into its model
//  - jsontree-r2-05: undoing a key / subtree DELETE said "restored" while the
//    tree kept showing it deleted: no row, a stale "{N keys}" until reload
//  - JT-05: after an inline edit of a null leaf, its ✎ opened the OLD null
//  - jsontree-r2-06: Enter on the focused Cancel button CREATED the key;
//    Enter on the type <select> created it with an empty value
//
// Run: node tests/tree_structural_revert_selfcheck.cjs   (driven by
// tests/test_undo_trail.py::test_tree_structural_revert_selfcheck)
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

const APP_JS = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 15); }); }
function jsonResp(obj) {
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(obj); } });
}

// The server's own shapes: /field/peek answers a missing path with ok:true,
// values[p] = null AND errors[p] (routes.field_peek, Phase 1).
function makeWorld(data) {
  const dom = new JSDOM('<!DOCTYPE html><html><body>'
    + '<div id="explorer-tree-state" class="json-tree"></div><div id="pending-tray"></div>'
    + '</body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/explorer' });
  const win = dom.window;
  win._server = {};          // dot_path -> value; absent = the server has no such key
  win._calls = [];
  win.fetch = function (url, opts) {
    url = String(url);
    win._calls.push({ url: url, body: opts && opts.body ? String(opts.body) : '' });
    if (url.indexOf('/field/peek') === 0) {
      const dp = decodeURIComponent(url.split('dot_path=')[1] || '');
      const values = {}, errors = {};
      if (Object.prototype.hasOwnProperty.call(win._server, dp)) values[dp] = win._server[dp];
      else { values[dp] = null; errors[dp] = "Key not found at path '" + dp + "'"; }
      return jsonResp({ ok: true, values: values, errors: errors, expected: {} });
    }
    if (url.indexOf('/field/create') === 0) return jsonResp({ ok: true });
    if (url.indexOf('/field/delete') === 0) return jsonResp({ ok: true, dangling_refs: 0 });
    if (url.indexOf('/field/refs') === 0) return jsonResp({ ok: true, total: 0 });
    if (url.indexOf('/field/edit') === 0) {
      const b = new win.URLSearchParams(String(opts && opts.body || ''));
      return jsonResp({ ok: true, stored: b.get('value'), stored_kind: 'str' });
    }
    if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
    return new Promise(function () {});
  };
  win.htmx = { ajax: function () { return Promise.resolve(); }, trigger: function () {}, process: function () {} };
  new win.Function(APP_JS).call(win);
  win.renderJsonTree('explorer-tree-state', JSON.parse(JSON.stringify(data)), { defaultDepth: 1, crud: true });
  return win;
}

function nodeAt(win, p) {
  return win.document.getElementById('explorer-tree-state')
    .querySelector('.tree-node[data-path="' + p + '"]');
}
function open(win, p) {
  const n = nodeAt(win, p);
  const t = n && n.querySelector(':scope > .tree-row > .tree-toggle.collapsed');
  if (t) t.click();
  return n;
}
function isOpen(n) {
  const k = n && n.querySelector(':scope > .tree-children');
  return !!(k && k.style.display !== 'none');
}
function summary(n) {
  const s = n && n.querySelector(':scope > .tree-row > .tree-summary');
  return s ? s.textContent : null;
}
function hover(win, n) {
  n.querySelector(':scope > .tree-row').dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
}
function undo(win, entries) {
  win.document.dispatchEvent(new win.CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: entries } }));
}

const DATA = { qubits: { q1: { f_01: 6.1e9, xy: { amp: 0.1, len: 40 }, extras: { a: 1, b: 2 } },
                         q2: { thread: null, f_01: 6.2e9 } } };

(async function main() {
  // ── JT-04: add a key through ＋, then Ctrl+Z ──────────────────────────
  {
    const win = makeWorld(DATA);
    const c = win.document.getElementById('explorer-tree-state');
    open(win, 'qubits'); open(win, 'qubits.q1'); open(win, 'qubits.q1.extras');
    ok(summary(nodeAt(win, 'qubits.q1.extras')) === '{2 keys}', 'fixture: extras reads {2 keys}');
    hover(win, nodeAt(win, 'qubits.q1.extras'));
    nodeAt(win, 'qubits.q1.extras').querySelector('.tree-act-add').click();
    const panel = nodeAt(win, 'qubits.q1.extras').querySelector('.tree-crud-panel');
    panel.querySelector('.tree-crud-key').value = 'undo_probe';
    panel.querySelector('.tree-crud-val').value = '7';
    win._server['qubits.q1.extras.undo_probe'] = 7;
    panel.querySelector('.tree-crud-ok').click();
    await tick(40);
    ok(!!nodeAt(win, 'qubits.q1.extras.undo_probe'), 'fixture: the added key is on screen');
    ok(summary(nodeAt(win, 'qubits.q1.extras')) === '{3 keys}', 'fixture: extras reads {3 keys} after the add');

    // the server undoes the creation: the key is GONE
    delete win._server['qubits.q1.extras.undo_probe'];
    undo(win, [{ dot_path: 'qubits.q1.extras.undo_probe', old_value_str: '', old_value_disp: '', created: true }]);
    await tick(40);
    ok(!nodeAt(win, 'qubits.q1.extras.undo_probe'), 'JT-04: the undone key has no row (was: "undo_probe: null")');
    ok(!('undo_probe' in c._treeData.qubits.q1.extras), 'JT-04: and is not in the tree model (was: key = null)');
    ok(summary(nodeAt(win, 'qubits.q1.extras')) === '{2 keys}', 'JT-04: extras reads {2 keys} again');
    ok(isOpen(nodeAt(win, 'qubits.q1.extras')), 'JT-04: extras stays open');
    ok(!c._flatIndex, 'JT-04: the search index is dropped for a rebuild');

    // the same through the tray's per-change ✕ (cellDiscarded)
    c._treeData.qubits.q1.extras.gone2 = 5;
    const ex = nodeAt(win, 'qubits.q1.extras');   // a key the server never had (model only)
    win.document.dispatchEvent(new win.CustomEvent('cellDiscarded', { detail: { dot_path: 'qubits.q1.extras.gone2', old_value_str: '' } }));
    await tick(40);
    ok(!('gone2' in c._treeData.qubits.q1.extras), 'JT-04: a discard of a creation removes the key from the model too');
    ok(ex !== nodeAt(win, 'qubits.q1.extras') && summary(nodeAt(win, 'qubits.q1.extras')) === '{2 keys}',
       'JT-04: and rebuilds the parent summary ({2 keys})');

    // a key that exists NOWHERE in the model (created on another surface):
    // the revert must not fabricate it as null
    undo(win, [{ dot_path: 'qubits.q1.elsewhere', old_value_str: '', old_value_disp: '', created: true }]);
    await tick(40);
    ok(!('elsewhere' in c._treeData.qubits.q1), 'JT-04: an absent key is never written into the model as null');
    ok(!nodeAt(win, 'qubits.q1.elsewhere'), 'JT-04: and gets no row');
  }

  // ── jsontree-r2-05: delete a subtree through ✕, then Ctrl+Z ─────────
  {
    const win = makeWorld(DATA);
    const c = win.document.getElementById('explorer-tree-state');
    open(win, 'qubits'); open(win, 'qubits.q1');
    ok(summary(nodeAt(win, 'qubits.q1')) === '{3 keys}', 'fixture: q1 reads {3 keys}');
    hover(win, nodeAt(win, 'qubits.q1.xy'));
    nodeAt(win, 'qubits.q1.xy').querySelector('.tree-act-del').click();
    const confirmBtn = Array.prototype.filter.call(
      nodeAt(win, 'qubits.q1.xy').querySelectorAll('.tree-act-del'), function (b) { return b.textContent === 'Delete'; })[0];
    confirmBtn.click();
    await tick(40);
    ok(!nodeAt(win, 'qubits.q1.xy'), 'fixture: the deleted subtree has no row');
    ok(summary(nodeAt(win, 'qubits.q1')) === '{2 keys}', 'fixture: q1 reads {2 keys} after the delete');
    open(win, 'qubits.q1');                         // the delete collapsed q1; the user re-opens it

    // the server restores it
    win._server['qubits.q1.xy'] = { amp: 0.1, len: 40 };
    undo(win, [{ dot_path: 'qubits.q1.xy', old_value_disp: '{…}', old_kind: 'other', deleted: true }]);
    await tick(40);
    const xy = nodeAt(win, 'qubits.q1.xy');
    ok(!!xy, 'r2-05: the restored subtree is back on screen (was: absent until reload)');
    ok(summary(nodeAt(win, 'qubits.q1')) === '{3 keys}', 'r2-05: q1 reads {3 keys} again (was: {2 keys})');
    ok(isOpen(nodeAt(win, 'qubits.q1')), 'r2-05: q1 stays open');
    ok(c._treeData.qubits.q1.xy && c._treeData.qubits.q1.xy.amp === 0.1, 'r2-05: the model holds the restored subtree');
    open(win, 'qubits.q1.xy');
    const amp = nodeAt(win, 'qubits.q1.xy.amp');
    ok(amp && amp.querySelector('.tree-val').textContent === '0.1', 'r2-05: and its children expand from it');

    // a restored key inside a NEVER-expanded branch: model only, no crash
    win._server['qubits.q2.restored'] = 3;
    undo(win, [{ dot_path: 'qubits.q2.restored', old_value_disp: '3', old_kind: 'num', deleted: true }]);
    await tick(40);
    ok(c._treeData.qubits.q2.restored === 3, 'r2-05: a key restored under a collapsed parent enters the model');
    ok(summary(nodeAt(win, 'qubits.q2')) === '{3 keys}', 'r2-05: and the collapsed parent\'s summary follows');

    // a VALUE revert keeps its old path (repaint in place, no rebuild)
    const q1Before = nodeAt(win, 'qubits.q1');
    win._server['qubits.q1.f_01'] = 5.9e9;
    undo(win, [{ dot_path: 'qubits.q1.f_01', old_value_disp: '5900000000', old_kind: 'num' }]);
    await tick(40);
    ok(nodeAt(win, 'qubits.q1') === q1Before, 'a value revert does not rebuild the parent');
    ok(c._treeData.qubits.q1.f_01 === 5.9e9, 'and the model follows the value revert');
  }

  // ── JT-05: inline-edit a null leaf, then its ✎ ──────────────────────
  {
    const win = makeWorld(DATA);
    const c = win.document.getElementById('explorer-tree-state');
    open(win, 'qubits'); open(win, 'qubits.q2');
    const th = nodeAt(win, 'qubits.q2.thread');
    ok(!!th.querySelector(':scope > .tree-row > .tree-json-edit-btn'), 'fixture: a null leaf offers the ✎ JSON editor');
    th.querySelector('.tree-val').click();
    const inp = th.querySelector('input.tree-edit-input');
    inp.value = '"t1"';
    inp.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    await tick(40);
    ok(c._treeData.qubits.q2.thread === 't1', 'fixture: the inline commit reached the model');
    const pb = th.querySelector(':scope > .tree-row > .tree-json-edit-btn');
    ok(!pb, 'JT-05: the leaf is no longer null, so its null-only ✎ is gone (was: kept, opening null)');
    ok(th._value === 't1', 'JT-05: the node carries the committed value (copy / paste read it)');

    // a leaf filled by an UNDO keeps its ✎ (the repaint does not rebuild):
    // the pencil must open what the leaf holds NOW, never the null it was built with
    const win2 = makeWorld(DATA);
    const c2 = win2.document.getElementById('explorer-tree-state');
    open(win2, 'qubits'); open(win2, 'qubits.q2');
    win2._server['qubits.q2.thread'] = 'r1';
    undo(win2, [{ dot_path: 'qubits.q2.thread', old_value_disp: 'r1', old_kind: 'str' }]);
    await tick(40);
    ok(c2._treeData.qubits.q2.thread === 'r1', 'fixture: the undo reached the model');
    const th2 = nodeAt(win2, 'qubits.q2.thread');
    const pb2 = th2.querySelector(':scope > .tree-row > .tree-json-edit-btn');
    ok(!!pb2, 'fixture: the repainted leaf still has its ✎');
    pb2.click();
    const ta = th2.querySelector('.tree-json-textarea');
    ok(ta && ta.value === '"r1"', 'JT-05: the ✎ opens the value the leaf holds now (' + (ta && ta.value) + '; was: null)');
  }

  // ── jsontree-r2-06: Enter in the add-key panel ───────────────────────
  {
    const win = makeWorld(DATA);
    open(win, 'qubits'); open(win, 'qubits.q1');
    function openPanel() {
      hover(win, nodeAt(win, 'qubits.q1.extras'));
      nodeAt(win, 'qubits.q1.extras').querySelector('.tree-act-add').click();
      return nodeAt(win, 'qubits.q1.extras').querySelector('.tree-crud-panel');
    }
    function creates() { return win._calls.filter(function (x) { return x.url.indexOf('/field/create') === 0; }).length; }
    function enterOn(el) {
      el.focus();
      el.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    }
    let p = openPanel();
    p.querySelector('.tree-crud-key').value = 'qa_cancel_enter';
    p.querySelector('.tree-crud-val').value = '1';
    enterOn(p.querySelector('.tree-crud-cancel'));
    await tick(30);
    ok(creates() === 0, 'r2-06: Enter on the focused Cancel creates nothing (was: POST /field/create)');
    p.remove();

    p = openPanel();
    p.querySelector('.tree-crud-key').value = 'qa_sel_enter';
    enterOn(p.querySelector('.tree-crud-type'));
    await tick(30);
    ok(creates() === 0, 'r2-06: Enter on the type select creates nothing (was: an empty-string key)');

    enterOn(p.querySelector('.tree-crud-key'));
    await tick(30);
    ok(creates() === 1, 'r2-06: Enter in the key box still submits');
    p = openPanel();
    p.querySelector('.tree-crud-key').value = 'qa_val_enter';
    p.querySelector('.tree-crud-val').value = '2';
    enterOn(p.querySelector('.tree-crud-val'));
    await tick(30);
    ok(creates() === 2, 'r2-06: Enter in the value box still submits');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all tree structural-revert checks passed');
  process.exit(0);
})().catch(function (e) { console.error('CRASH: ' + (e && e.stack || e)); process.exit(1); });
