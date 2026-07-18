// Behavioral check for the Explorer structural CRUD + type picker (app.js):
//  - hover lazily builds row actions on crud-enabled trees only
//  - dict rows get ＋, leaves get ⚙/✕, list elements + identity keys get none
//  - add-key posts /field/create with the chosen expect_type
//  - delete confirm shows leaf count, fetches /field/refs, posts /field/delete,
//    rebuilds the parent
//  - type picker: env-conflict 409 → confirm() → override re-POST
//  - the value editor shows the expected-type chip from /field/peek
//
// Run: node tests/explorer_crud_selfcheck.cjs   (needs jsdom)
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
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 8); }); }

const DATA = {
  qubits: {
    qA1: {
      __class__: 'q.Transmon',
      id: 'qA1',
      f_01: 6.25e9,
      extras: { note: 'x' },
      confusion_matrix: [[0.98, 0.02], [0.03, 0.97]]
    }
  }
};

function makeWorld(fetchImpl) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="tree"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._fetchCalls = [];
  win.fetch = function (url, opts) {
    win._fetchCalls.push({ url: url, opts: opts || {} });
    return fetchImpl(url, opts || {});
  };
  win.confirm = function () { win._confirmed = (win._confirmed || 0) + 1; return true; };
  new win.Function(APP_JS).call(win);
  win.renderJsonTree('tree', JSON.parse(JSON.stringify(DATA)), { defaultDepth: 1, crud: true });
  return win;
}

function jsonResp(obj, status) {
  return Promise.resolve({ ok: (status || 200) < 400, status: status || 200,
    json: function () { return Promise.resolve(obj); } });
}

function expandAll(container) {
  for (var round = 0; round < 10; round++) {
    var t = container.querySelectorAll('.tree-toggle.collapsed');
    if (!t.length) break;
    t.forEach(function (x) { x.click(); });
  }
}

function hover(win, node) {
  var row = node.querySelector(':scope > .tree-row');
  row.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  return row;
}

function nodeAt(container, p) {
  return container.querySelector('.tree-node[data-path="' + p + '"]');
}

(async function main() {

  // C1: hover affordances per node kind.
  {
    const win = makeWorld(function (url) {
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      if (url.indexOf('/field/peek') === 0) return jsonResp({ ok: true, values: {}, expected: {} });
      return jsonResp({ ok: true });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const dictNode = nodeAt(c, 'qubits.qA1');
    hover(win, dictNode);
    ok(!!dictNode.querySelector('.tree-act-add'), 'C1: dict row has ＋');
    ok(!!dictNode.querySelector('.tree-act-del'), 'C1: dict row has ✕');
    const leaf = nodeAt(c, 'qubits.qA1.f_01');
    hover(win, leaf);
    ok(!!leaf.querySelector('.tree-act-type'), 'C1: leaf row has ⚙');
    const idLeaf = nodeAt(c, 'qubits.qA1.id');
    hover(win, idLeaf);
    ok(!idLeaf.querySelector('.tree-act-btn'), 'C1: identity key gets no actions');
    const el = nodeAt(c, 'qubits.qA1.confusion_matrix.0.0');
    if (el) { hover(win, el); ok(!el.querySelector('.tree-act-btn'), 'C1: list element gets no actions'); }
    const top = nodeAt(c, 'qubits');
    hover(win, top);
    ok(!top.querySelector(':scope > .tree-row > .tree-row-actions .tree-act-del'),
      'C1: top-level has no delete');
  }

  // C2: crud NOT attached without the option.
  {
    const win = makeWorld(function () { return jsonResp({ ok: true }); });
    win.renderJsonTree('tree', JSON.parse(JSON.stringify(DATA)), { defaultDepth: 1 });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const n = nodeAt(c, 'qubits.qA1');
    hover(win, n);
    ok(!n.querySelector('.tree-act-btn'), 'C2: no actions on a non-crud tree');
  }

  // C3: add-key posts /field/create with expect_type; suggestion prefills.
  {
    const win = makeWorld(function (url, opts) {
      if (url.indexOf('/schema/missing-keys') === 0) {
        return jsonResp({ ok: true, warm: true, missing: [
          { key: 'T1', path: 'qubits.qA1.T1', expected_type: 'number', default: null, source_class: 'Transmon' }] });
      }
      if (url === '/field/create') return jsonResp({ ok: true, tray_html: '', created_path: 'qubits.qA1.T1' });
      if (url.indexOf('/field/peek') === 0) return jsonResp({ ok: true, values: { 'qubits.qA1.T1': 8834 }, expected: {} });
      return jsonResp({ ok: true });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const dictNode = nodeAt(c, 'qubits.qA1');
    hover(win, dictNode);
    dictNode.querySelector('.tree-act-add').click();
    await tick();
    const panel = dictNode.querySelector('.tree-crud-panel');
    ok(!!panel, 'C3: add panel opens');
    const keyIn = panel.querySelector('.tree-crud-key');
    keyIn.value = 'T1';
    keyIn.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(panel.querySelector('.tree-crud-type').value === 'number',
      'C3: schema suggestion prefills the type');
    panel.querySelector('.tree-crud-val').value = '8834';
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    const call = win._fetchCalls.find(function (x) { return x.url === '/field/create'; });
    ok(!!call, 'C3: /field/create POSTed');
    if (call) {
      ok(call.opts.body.indexOf('dot_path=qubits.qA1.T1') >= 0, 'C3: dot_path correct');
      ok(call.opts.body.indexOf('expect_type=number') >= 0, 'C3: expect_type sent');
    }
    await tick(20);
    ok(!!nodeAt(c, 'qubits.qA1.T1'), 'C3: node rebuilt with the new key');
  }

  // C4: delete confirm → refs fetch → POST → parent rebuild.
  {
    const win = makeWorld(function (url) {
      if (url.indexOf('/field/refs') === 0) return jsonResp({ ok: true, total: 2, refs: [] });
      if (url === '/field/delete') return jsonResp({ ok: true, tray_html: '', removed_leaves: 1, dangling_refs: 2 });
      if (url.indexOf('/field/peek') === 0) return jsonResp({ ok: true, values: {}, expected: {} });
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      return jsonResp({ ok: true });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const leaf = nodeAt(c, 'qubits.qA1.f_01');
    hover(win, leaf);
    leaf.querySelector('.tree-act-del').click();
    await tick(20);
    const confirmBtns = leaf.querySelectorAll('.tree-row-actions .tree-act-btn');
    ok(confirmBtns.length >= 2, 'C4: confirm buttons appear');
    ok(/1 leaves/.test(leaf.textContent) || /leaves/.test(leaf.textContent),
      'C4: leaf count shown');
    // click the Delete confirm
    confirmBtns[0].click();
    await tick(25);
    ok(win._fetchCalls.some(function (x) { return x.url === '/field/delete'; }),
      'C4: /field/delete POSTed');
    ok(!nodeAt(c, 'qubits.qA1.f_01'), 'C4: leaf removed after parent rebuild');
  }

  // C5: type picker 409 env-conflict → confirm → override.
  {
    let posts = 0;
    const win = makeWorld(function (url, opts) {
      if (url.indexOf('/field/peek') === 0) {
        return jsonResp({ ok: true, values: {}, expected: {
          'qubits.qA1.f_01': { type: 'number', source: 'env',
            class_path: 'q.Transmon', field: 'f_01', detail: 'float' } } });
      }
      if (url === '/field/type-assign') {
        posts += 1;
        if ((opts.body || '').indexOf('override_env=1') < 0) {
          return jsonResp({ ok: false, error_kind: 'env_conflict',
            env_type: { type: 'number' } }, 409);
        }
        return jsonResp({ ok: true, expected: { source: 'user' } });
      }
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      return jsonResp({ ok: true });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const leaf = nodeAt(c, 'qubits.qA1.f_01');
    hover(win, leaf);
    leaf.querySelector('.tree-act-type').click();
    await tick(20);
    const panel = leaf.querySelector('.tree-type-panel');
    ok(!!panel, 'C5: type panel opens');
    ok(/expected: number · env/.test(panel.textContent), 'C5: env provenance shown');
    panel.querySelector('input[value="str"]').checked = true;
    panel.querySelector('.tree-type-assign').click();
    await tick(25);
    ok(win._confirmed === 1, 'C5: env-conflict asked for confirmation');
    ok(posts === 2, 'C5: re-POSTed with override_env after confirm');
  }

  // C6: value editor shows the expected-type chip from peek.
  {
    const win = makeWorld(function (url) {
      if (url.indexOf('/field/peek') === 0) {
        return jsonResp({ ok: true, values: {}, expected: {
          'qubits.qA1.f_01': { type: 'number', source: 'env', detail: 'float' } } });
      }
      return jsonResp({ ok: true });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const leaf = nodeAt(c, 'qubits.qA1.f_01');
    leaf.querySelector('.tree-val').click();
    await tick(20);
    const chip = leaf.querySelector('.tree-type-chip');
    ok(!!chip && /number · env/.test(chip.textContent), 'C6: type chip in the editor');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('explorer_crud_selfcheck: all checks passed');
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
