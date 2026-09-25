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

function makeWorld(fetchImpl, policy, data) {
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
  // AFTER app.js — it defines the real copyWithFeedback, which would otherwise
  // replace this stub and the copy would go to a clipboard jsdom has not got.
  win._copied = [];
  win.copyWithFeedback = function (txt) { win._copied.push(txt); };
  // The explorer page injects this (leaf_classify.readonly_policy). Left absent
  // by default so every other check here runs against what it always did.
  if (policy) win._treeReadOnly = policy;
  win.renderJsonTree('tree', JSON.parse(JSON.stringify(data || DATA)),
                     { defaultDepth: 1, crud: true });
  return win;
}

// The server's own payload, spelled as leaf_classify.readonly_policy() emits it.
const RO_POLICY = {
  membership_tops: ['active_qubit_names', 'active_qubit_pair_names', 'active_twpa_names'],
  membership_reason: 'chip-membership array \u2014 edit via the chip add/remove controls, not here',
  skip_leaves: ['__class__', 'id'],
  skip_reason: 'identity / type key \u2014 read-only'
};

const RO_DATA = {
  active_qubit_names: ['q1', 'q2'],
  qubits: { qA1: { __class__: 'q.Transmon', id: 'qA1', f_01: 6.25e9 } }
};

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
    // docs/126 ④ amended: EVERY row now carries the ⧉ copy action (customer
    // request) — identity keys and list elements get copy and NOTHING else
    // (no add / type / delete, unchanged).
    const idLeaf = nodeAt(c, 'qubits.qA1.id');
    hover(win, idLeaf);
    ok(!!idLeaf.querySelector('.tree-act-copy'), 'C1: identity key gets the ⧉ copy');
    ok(!idLeaf.querySelector('.tree-act-del') && !idLeaf.querySelector('.tree-act-type')
       && !idLeaf.querySelector('.tree-act-add'),
       'C1: identity key gets nothing destructive');
    const el = nodeAt(c, 'qubits.qA1.confusion_matrix.0.0');
    if (el) {
      hover(win, el);
      ok(!!el.querySelector('.tree-act-copy'), 'C1: list element gets the ⧉ copy');
      ok(!el.querySelector('.tree-act-del') && !el.querySelector('.tree-act-add'),
         'C1: list element gets nothing destructive');
    }
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
    ok(panel.querySelector('.tree-crud-type').value === 'real',
      'C3: schema suggestion (legacy "number" token) prefills the real choice');
    panel.querySelector('.tree-crud-val').value = '8834';
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    const call = win._fetchCalls.find(function (x) { return x.url === '/field/create'; });
    ok(!!call, 'C3: /field/create POSTed');
    if (call) {
      ok(call.opts.body.indexOf('dot_path=qubits.qA1.T1') >= 0, 'C3: dot_path correct');
      ok(call.opts.body.indexOf('expect_type=real') >= 0, 'C3: expect_type sent');
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
    // JT-13: the rebuilt parent used to come back COLLAPSED and lazy, so the
    // check above passed vacuously (no child was in the DOM at all) and the
    // user lost the branch they were deleting from.
    const par = nodeAt(c, 'qubits.qA1');
    ok(!!par && !par.querySelector(':scope > .tree-row > .tree-toggle.collapsed'),
      'C4: the parent stays open after the delete');
    ok(!!nodeAt(c, 'qubits.qA1.id'), 'C4: a sibling leaf is still on screen');
    ok(!!nodeAt(c, 'qubits.qA1.confusion_matrix.0.0'),
      'C4: an open sibling subtree stays open too');
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

  // C7: a membership element never opens an editor. It used to: the row took
  //     a typed value and only /field/edit said "not here", after the fact.
  {
    const win = makeWorld(function () { return jsonResp({ ok: true }); },
                          RO_POLICY, RO_DATA);
    const c = win.document.getElementById('tree');
    expandAll(c);
    const el = nodeAt(c, 'active_qubit_names.0');
    ok(!!el, 'C7: the membership element renders');
    const val = el.querySelector('.tree-val');
    ok(val.classList.contains('tree-val-readonly'), 'C7: marked read-only');
    ok(/chip-membership array/.test(val.title), 'C7: the title carries the reason');
    val.click();
    await tick(20);
    ok(!el.querySelector('.tree-edit-input'), 'C7: no edit box opens');
    ok(win._copied.length === 1, 'C7: the click copies instead');
    ok(win._fetchCalls.filter(function (f) {
      return String(f.url).indexOf('/field/edit') === 0; }).length === 0,
      'C7: nothing was POSTed to /field/edit');
  }

  // C7b (jsontree-r2-25): the whole-value JSON editor ✎ is a write door too.
  //      A membership array (and a null membership top / identity leaf) used
  //      to offer it; the editor opened with the list and only Save refused.
  //      An ordinary container keeps its ✎ (guards over-refusal, and keeps
  //      the pin from passing vacuously on a tree with no ✎ at all).
  {
    const DATA_7B = {
      active_qubit_names: ['q1', 'q2'],
      active_twpa_names: null,
      qubits: { qA1: { __class__: 'q.Transmon', id: null, f_01: 6.25e9, extras: null } }
    };
    const win = makeWorld(function () { return jsonResp({ ok: true }); },
                          RO_POLICY, DATA_7B);
    const c = win.document.getElementById('tree');
    expandAll(c);
    const btnOf = function (p) {
      return nodeAt(c, p).querySelector(':scope > .tree-row > .tree-json-edit-btn');
    };
    ok(!btnOf('active_qubit_names'), 'C7b: no JSON ✎ on a membership array');
    ok(/chip-membership array/.test(
      nodeAt(c, 'active_qubit_names').querySelector(':scope > .tree-row > .tree-summary').title),
      'C7b: the array row says why instead');
    ok(!btnOf('active_twpa_names'), 'C7b: no null-leaf ✎ on a null membership top');
    ok(!btnOf('qubits.qA1.id'), 'C7b: no null-leaf ✎ on a null identity key');
    ok(!!btnOf('qubits.qA1'), 'C7b: an ordinary container keeps its ✎');
    ok(!!btnOf('qubits.qA1.extras'), 'C7b: an ordinary null leaf keeps its ✎');
  }

  // C8: an identity key is the same policy, and the reason differs.
  {
    const win = makeWorld(function () { return jsonResp({ ok: true }); },
                          RO_POLICY, RO_DATA);
    const c = win.document.getElementById('tree');
    expandAll(c);
    const el = nodeAt(c, 'qubits.qA1.__class__');
    const val = el.querySelector('.tree-val');
    ok(val.classList.contains('tree-val-readonly'), 'C8: identity key marked read-only');
    ok(/identity \/ type key/.test(val.title), 'C8: identity reason, not the membership one');
    val.click();
    await tick(20);
    ok(!el.querySelector('.tree-edit-input'), 'C8: no edit box on an identity key');
  }

  // C9: an ordinary leaf is untouched — the gate refuses only what the doors do.
  {
    const win = makeWorld(function () { return jsonResp({ ok: true, values: {}, expected: {} }); },
                          RO_POLICY, RO_DATA);
    const c = win.document.getElementById('tree');
    expandAll(c);
    const el = nodeAt(c, 'qubits.qA1.f_01');
    const val = el.querySelector('.tree-val');
    ok(!val.classList.contains('tree-val-readonly'), 'C9: a normal leaf is not marked');
    val.click();
    await tick(20);
    ok(!!el.querySelector('.tree-edit-input'), 'C9: a normal leaf still edits');
  }

  // C10: no structural action anywhere under a membership top — /field/create
  //      and /field/delete both refuse one.
  //
  //      The shape here is a DICT, not the list a healthy chip carries, and
  //      that is deliberate: for a LIST the existing inList/isDict/topLevel
  //      rules already suppress every action, so a list fixture pins nothing
  //      (it passed with the gate reverted — a vacuous pin). A malformed chip
  //      is exactly where the gate earns its place: without it this node is
  //      offered ⚙ and ✕ that the door then refuses.
  {
    const MALFORMED = { active_qubit_names: { q1: true }, qubits: {} };
    const win = makeWorld(function () { return jsonResp({ ok: true }); },
                          RO_POLICY, MALFORMED);
    const c = win.document.getElementById('tree');
    expandAll(c);
    const top = nodeAt(c, 'active_qubit_names');
    hover(win, top);
    ok(!top.querySelector('.tree-act-add'), 'C10: no ＋ on a membership top');
    ok(!!top.querySelector('.tree-act-copy'), 'C10: copy still offered');

    const leaf = nodeAt(c, 'active_qubit_names.q1');
    hover(win, leaf);
    ok(!leaf.querySelector('.tree-act-del'), 'C10: no ✕ under a membership top');
    ok(!leaf.querySelector('.tree-act-type'), 'C10: no ⚙ under a membership top');
  }

  // C11 (JT-12): a re-point to nowhere lands (by design) and is SAID inline.
  {
    const win = makeWorld(function (url) {
      if (url === '/field/edit') return jsonResp({ ok: true, tray_html: '',
        stored: 6.3e9, stored_kind: 'real',
        warning: '#/ports/nowhere/x does not resolve on this chip' });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    win._toasts = [];
    win.showToast = function (m, lvl) { win._toasts.push([m, lvl]); };
    const c = win.document.getElementById('tree');
    expandAll(c);
    const val = nodeAt(c, 'qubits.qA1.f_01').querySelector('.tree-val');
    val.click();
    await tick(10);
    const inp = val.querySelector('input');
    inp.value = '6.3e9';
    inp.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await tick(30);
    ok(win._toasts.some(function (t) { return t[1] === 'warning' && /does not resolve/.test(t[0]); }),
      'C11: the /field/edit warning is shown as a warning toast');
  }

  // C12 (JT-13): the container JSON editor -- an untouched Save posts
  //      NOTHING (the server never no-ops), and a real Save keeps the node
  //      and its open descendants open.
  {
    const win = makeWorld(function (url) {
      if (url === '/field/edit') return jsonResp({ ok: true, tray_html: '' });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const edits = function () {
      return win._fetchCalls.filter(function (x) { return x.url === '/field/edit'; }).length;
    };
    let cm = nodeAt(c, 'qubits.qA1.confusion_matrix');
    cm.querySelector(':scope > .tree-row > .tree-json-edit-btn').click();
    let ed = cm.querySelector(':scope > .tree-json-editor');
    ok(!!ed, 'C12: the JSON editor opens');
    ed.querySelector('.tree-json-editor-bar button').click();   // Save
    await tick(20);
    ok(edits() === 0, 'C12: Save with nothing changed POSTs nothing (' + edits() + ')');
    ok(!cm.querySelector(':scope > .tree-json-editor'), 'C12: ...and closes the editor');
    cm = nodeAt(c, 'qubits.qA1.confusion_matrix');
    cm.querySelector(':scope > .tree-row > .tree-json-edit-btn').click();
    ed = cm.querySelector(':scope > .tree-json-editor');
    const ta = ed.querySelector('textarea');
    ta.value = '[[0.95,0.05],[0.1,0.9]]';
    ta.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
    await tick(25);
    ok(edits() === 1, 'C12: a real edit POSTs once');
    cm = nodeAt(c, 'qubits.qA1.confusion_matrix');
    ok(!!cm && !cm.querySelector(':scope > .tree-row > .tree-toggle.collapsed'),
      'C12: the saved node comes back OPEN');
    ok(!!nodeAt(c, 'qubits.qA1.confusion_matrix.0.0'),
      'C12: its open row 0 is open again, showing the new value');
    const v00 = nodeAt(c, 'qubits.qA1.confusion_matrix.0.0');
    ok(v00 && /0\.95/.test(v00.textContent), 'C12: ...with the saved value');
  }

  // C13 (JT-13): a boolean takes the coercer's words -- "1" on true is true,
  //      so it must not stage a "True -> True" no-op.
  {
    const BOOL = { qubits: { qA1: { active: true, other: false } } };
    const win = makeWorld(function (url) {
      if (url === '/field/edit') return jsonResp({ ok: true, tray_html: '', stored: false, stored_kind: 'bool' });
      return jsonResp({ ok: true, values: {}, expected: {} });
    }, null, BOOL);
    const c = win.document.getElementById('tree');
    expandAll(c);
    const edits = function () {
      return win._fetchCalls.filter(function (x) { return x.url === '/field/edit'; }).length;
    };
    async function type(path, text) {
      const v = nodeAt(c, path).querySelector('.tree-val');
      v.click();
      await tick(10);
      const inp = v.querySelector('input');
      inp.value = text;
      inp.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await tick(25);
    }
    await type('qubits.qA1.active', '1');
    ok(edits() === 0, 'C13: "1" on true posts nothing');
    await type('qubits.qA1.active', ' YES ');
    ok(edits() === 0, 'C13: "YES" on true posts nothing');
    await type('qubits.qA1.other', 'off');
    ok(edits() === 0, 'C13: "off" on false posts nothing');
    await type('qubits.qA1.active', '0');
    ok(edits() === 1, 'C13: "0" on true is a real change and posts');
  }

  // C14 (JT-14): the type picker -- the env's own type is a no-op reply
  //      (no confirm), "pick a type" clears on a pick, Esc closes it.
  {
    const win = makeWorld(function (url, opts) {
      if (url.indexOf('/field/peek') === 0) {
        return jsonResp({ ok: true, values: {}, expected: {
          'qubits.qA1.f_01': { type: 'real', source: 'env',
            class_path: 'q.Transmon', field: 'f_01', detail: 'float' } } });
      }
      if (url === '/field/type-assign') {
        return jsonResp({ ok: true, noop: true, removed: false, already: 'real',
          expected: { type: 'real', source: 'env' } });
      }
      return jsonResp({ ok: true, warm: false, missing: [] });
    });
    win._toasts = [];
    win.showToast = function (m, lvl) { win._toasts.push([m, lvl]); };
    const c = win.document.getElementById('tree');
    expandAll(c);
    const leaf = nodeAt(c, 'qubits.qA1.f_01');
    hover(win, leaf);
    leaf.querySelector('.tree-act-type').click();
    await tick(20);
    let panel = leaf.querySelector('.tree-type-panel');
    ok(!!panel && panel.contains(win.document.activeElement),
      'C14: focus starts inside the panel (' + (win.document.activeElement && win.document.activeElement.tagName) + ')');
    panel.querySelector('.tree-type-assign').click();              // no pick
    const err = panel.querySelector('.tree-crud-err');
    ok(/pick a type/.test(err.textContent), 'C14: Assign without a pick asks for one');
    const real = panel.querySelector('input[value="real"]');
    real.checked = true;
    real.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(err.textContent === '', 'C14: the stale "pick a type" clears on a pick');
    panel.querySelector('.tree-type-assign').click();
    await tick(25);
    ok(!win._confirmed, 'C14: the env\'s own type asks no "Override real with real?"');
    ok(!leaf.querySelector('.tree-type-panel'), 'C14: the no-op closes the panel');
    ok(win._toasts.some(function (t) { return /Already real/.test(t[0]); }),
      'C14: and says it is already that type');
    hover(win, leaf);
    leaf.querySelector('.tree-act-type').click();
    await tick(20);
    panel = leaf.querySelector('.tree-type-panel');
    win.document.activeElement.dispatchEvent(
      new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(!leaf.querySelector('.tree-type-panel'), 'C14: Esc from the focused panel closes it');
  }

  // C15 (jsontree-r2-24): a key with "." is refused in the panel, by name;
  //      a plain key travels on its own (`key=`) for the route's backstop.
  {
    const win = makeWorld(function (url) {
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      if (url === '/field/create') return jsonResp({ ok: true, tray_html: '' });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const ex = nodeAt(c, 'qubits.qA1.extras');
    hover(win, ex);
    ex.querySelector('.tree-act-add').click();
    await tick();
    const panel = ex.querySelector('.tree-crud-panel');
    panel.querySelector('.tree-crud-key').value = 'v1.2';
    panel.querySelector('.tree-crud-val').value = '5';
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    const creates = function () {
      return win._fetchCalls.filter(function (x) { return x.url === '/field/create'; });
    };
    ok(creates().length === 0, 'C15: a dotted key is never POSTed');
    ok(/cannot contain/.test(panel.querySelector('.tree-crud-err').textContent),
      'C15: and the panel says why');
    panel.querySelector('.tree-crud-key').value = 'v12';
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    ok(creates().length === 1 && /(^|&)key=v12(&|$)/.test(creates()[0].opts.body),
      'C15: a plain key is sent with key= (' + (creates()[0] && creates()[0].opts.body) + ')');
  }

  // C16 (jsontree-r2-22): a suggestion whose class default is null says an
  //      empty submit means null.
  {
    const win = makeWorld(function (url) {
      if (url.indexOf('/schema/missing-keys') === 0) {
        return jsonResp({ ok: true, warm: true, missing: [
          { key: 'thread', expected_type: 'str', default: null, source_class: 'XYDriveMW' }] });
      }
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const dictNode = nodeAt(c, 'qubits.qA1');
    hover(win, dictNode);
    dictNode.querySelector('.tree-act-add').click();
    await tick();
    const panel = dictNode.querySelector('.tree-crud-panel');
    const keyIn = panel.querySelector('.tree-crud-key');
    keyIn.value = 'thread';
    keyIn.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(panel.querySelector('.tree-crud-val').placeholder.indexOf('null (class default)') >= 0,
      'C16: the value box says empty = null for a null default');
  }

  // C16b (jsontree-r2-22 review): an explicit dict / list / matrix with an
  //      empty value is the empty container (the route makes {} / []); the
  //      box says so, and a None-default suggestion -- whose box says
  //      "null (class default)" -- sends empty_is_default so it stays null.
  {
    const win = makeWorld(function (url) {
      if (url.indexOf('/schema/missing-keys') === 0) {
        return jsonResp({ ok: true, warm: true, missing: [
          { key: 'slots', expected_type: 'dict', default: null, source_class: 'X' }] });
      }
      if (url === '/field/create') return jsonResp({ ok: false, error: 'kept open' });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const ex = nodeAt(c, 'qubits.qA1.extras');
    hover(win, ex);
    ex.querySelector('.tree-act-add').click();
    await tick();
    const panel = ex.querySelector('.tree-crud-panel');
    const keyIn = panel.querySelector('.tree-crud-key');
    const typeSel = panel.querySelector('.tree-crud-type');
    const valIn = panel.querySelector('.tree-crud-val');
    const pick = function (t) { typeSel.value = t; typeSel.dispatchEvent(new win.Event('change', { bubbles: true })); };
    const last = function () {
      const cr = win._fetchCalls.filter(function (x) { return x.url === '/field/create'; });
      return cr.length ? String(cr[cr.length - 1].opts.body) : '';
    };
    keyIn.value = 'bag';
    keyIn.dispatchEvent(new win.Event('change', { bubbles: true }));
    pick('dict');
    ok(valIn.placeholder === 'value (empty = {})', 'C16b: dict says empty = {} (' + valIn.placeholder + ')');
    pick('matrix');
    ok(valIn.placeholder === 'value (empty = [])', 'C16b: matrix says empty = [] (' + valIn.placeholder + ')');
    pick('str');
    ok(/empty = null/.test(valIn.placeholder), 'C16b: str says empty = null');
    pick('dict');
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    ok(/(^|&)expect_type=dict(&|$)/.test(last()) && !/empty_is_default/.test(last()),
      'C16b: an explicit dict posts no empty_is_default (' + last() + ')');
    keyIn.value = 'slots';
    keyIn.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(typeSel.value === 'dict' && valIn.placeholder === 'null (class default)',
      'C16b: the None-default suggestion reads "null (class default)" under dict');
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    ok(/(^|&)empty_is_default=1(&|$)/.test(last()), 'C16b: and sends empty_is_default=1 (' + last() + ')');
    valIn.value = '{"a": 1}';
    panel.querySelector('.tree-crud-ok').click();
    await tick(20);
    ok(!/empty_is_default/.test(last()), 'C16b: a typed value never carries the flag');
  }

  // C17 (jsontree-r2-29): a wrong-chip refusal on ＋ / ✕ carries its way
  //      forward. Only a reload re-issues the page's chip token, so the
  //      message alone was a dead end. A NON-chip refusal gets no button.
  {
    const MISMATCH = { ok: false, chip_mismatch: true, loaded_chip: 'chipB',
      error: "Not applied: this app now has 'chipB' loaded ... reload this page" };
    const win = makeWorld(function (url) {
      if (url === '/field/create') return jsonResp(MISMATCH, 409);
      if (url === '/field/delete') return jsonResp(MISMATCH, 409);
      if (url === '/field/type-assign') return jsonResp(MISMATCH, 409);
      if (url.indexOf('/field/refs') === 0) return jsonResp({ ok: true, total: 0, refs: [] });
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const dictNode = nodeAt(c, 'qubits.qA1');
    hover(win, dictNode);
    dictNode.querySelector('.tree-act-add').click();
    await tick();
    const panel = dictNode.querySelector('.tree-crud-panel');
    panel.querySelector('.tree-crud-key').value = 'thing';
    panel.querySelector('.tree-crud-val').value = '1';
    panel.querySelector('.tree-crud-ok').click();
    await tick(25);
    const err = panel.querySelector('.tree-crud-err');
    ok(/chipB/.test(err.textContent), 'C17: the add refusal is shown');
    ok(!!err.querySelector('.tree-reload-btn'), 'C17: the add refusal offers Reload page');

    const leaf = nodeAt(c, 'qubits.qA1.f_01');
    hover(win, leaf);
    leaf.querySelector('.tree-act-del').click();
    await tick(20);
    leaf.querySelectorAll('.tree-row-actions .tree-act-btn')[0].click();
    await tick(25);
    const chip = leaf.querySelector(':scope > .tree-row > .tree-edit-err');
    ok(!!chip && /chipB/.test(chip.textContent), 'C17: the delete refusal is shown');
    ok(!!chip && !!chip.querySelector('.tree-reload-btn'), 'C17: the delete refusal offers Reload page');
    ok(!!nodeAt(c, 'qubits.qA1.f_01'), 'C17: the refused delete left the leaf on screen');

    const note = nodeAt(c, 'qubits.qA1.extras.note');
    hover(win, note);
    note.querySelector('.tree-act-type').click();
    await tick(20);
    const tpanel = note.querySelector('.tree-type-panel');
    tpanel.querySelector('input[value="str"]').checked = true;
    tpanel.querySelector('.tree-type-assign').click();
    await tick(25);
    const terr = tpanel.querySelector('.tree-crud-err');
    ok(/chipB/.test(terr.textContent) && !!terr.querySelector('.tree-reload-btn'),
      'C17: the type-assignment refusal offers Reload page');
  }
  {
    const win = makeWorld(function (url) {
      if (url === '/field/create') return jsonResp({ ok: false, error: 'Parent is not a dict' }, 400);
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    const c = win.document.getElementById('tree');
    expandAll(c);
    const dictNode = nodeAt(c, 'qubits.qA1');
    hover(win, dictNode);
    dictNode.querySelector('.tree-act-add').click();
    await tick();
    const panel = dictNode.querySelector('.tree-crud-panel');
    panel.querySelector('.tree-crud-key').value = 'thing';
    panel.querySelector('.tree-crud-ok').click();
    await tick(25);
    const err = panel.querySelector('.tree-crud-err');
    ok(/not a dict/.test(err.textContent) && !err.querySelector('.tree-reload-btn'),
      'C17: an ordinary refusal offers no Reload');
  }

  // C18 (jsontree-r2-18; C11 in fix/qa2-jt-view): SM unreachable. A fetch that never reaches the
  //      server rejects with a TypeError; every tree write used to revert /
  //      vanish in SILENCE (the global htmx:sendError toast never fires for a
  //      raw fetch). Each one must now say the app could not be reached, and
  //      a reply that is not JSON must NOT be blamed on the network.
  {
    const down = function (url) {
      if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
      return Promise.reject(new TypeError('Failed to fetch'));
    };
    // inline edit
    let win = makeWorld(down);
    let c = win.document.getElementById('tree');
    expandAll(c);
    let leaf = nodeAt(c, 'qubits.qA1.f_01');
    let val = leaf.querySelector('.tree-val');
    const before = val.textContent;
    val.click();
    await tick(10);
    let inp = leaf.querySelector('.tree-edit-input');
    inp.value = '1.5e-05';
    inp.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await tick(30);
    let chip = leaf.querySelector(':scope > .tree-row .tree-edit-err');
    ok(!!chip && /reach the app/.test(chip.textContent),
       'C18: a failed inline edit says the app could not be reached (' + (chip && chip.textContent) + ')');
    ok(val.textContent === before, 'C18: the display still reverts to the stored value');
    ok(!!chip && chip.title.indexOf("Couldn't reach the app") === 0,
       'C18: the chip ellipsizes, so its hover title carries the whole reason');

    // delete: the refs label never sits at "refs: …", the failed POST is named
    win = makeWorld(down);
    c = win.document.getElementById('tree');
    expandAll(c);
    leaf = nodeAt(c, 'qubits.qA1.f_01');
    hover(win, leaf);
    leaf.querySelector('.tree-act-del').click();
    await tick(20);
    const lbl = leaf.querySelector('.tree-del-confirm');
    ok(!!lbl && lbl.textContent.indexOf('refs: …') < 0 && /refs: unknown/.test(lbl.textContent),
       'C18: an unreachable refs pre-fetch says "refs: unknown" (' + (lbl && lbl.textContent) + ')');
    leaf.querySelectorAll('.tree-row-actions .tree-act-btn')[0].click();
    await tick(25);
    chip = leaf.querySelector(':scope > .tree-row .tree-edit-err');
    ok(!!chip && /reach the app/.test(chip.textContent),
       'C18: a failed delete says the app could not be reached');
    ok(!!nodeAt(c, 'qubits.qA1.f_01'), 'C18: the row is still there (nothing deleted)');

    // add key
    win = makeWorld(down);
    c = win.document.getElementById('tree');
    expandAll(c);
    const dictNode = nodeAt(c, 'qubits.qA1');
    hover(win, dictNode);
    dictNode.querySelector('.tree-act-add').click();
    await tick();
    const panel = dictNode.querySelector('.tree-crud-panel');
    panel.querySelector('.tree-crud-key').value = 'T9';
    panel.querySelector('.tree-crud-val').value = '1';
    panel.querySelector('.tree-crud-ok').click();
    await tick(25);
    ok(/reach the app/.test(panel.querySelector('.tree-crud-err').textContent),
       'C18: a failed add says the app could not be reached');

    // a reply that is not JSON (an HTML 500) is NOT "the app is not running"
    win = makeWorld(function (url) {
      if (url === '/field/edit') return Promise.resolve({ ok: false, status: 500,
        json: function () { return Promise.reject(new SyntaxError('Unexpected token <')); } });
      return jsonResp({ ok: true, values: {}, expected: {} });
    });
    c = win.document.getElementById('tree');
    expandAll(c);
    leaf = nodeAt(c, 'qubits.qA1.f_01');
    leaf.querySelector('.tree-val').click();
    await tick(10);
    inp = leaf.querySelector('.tree-edit-input');
    inp.value = '7';
    inp.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await tick(30);
    chip = leaf.querySelector(':scope > .tree-row .tree-edit-err');
    ok(!!chip && !/reach the app/.test(chip.textContent) && /Unexpected reply/.test(chip.textContent),
       'C18: a non-JSON reply is named as such, not blamed on the network');
  }

  // JT-22: the copy pill's label follows the paste buttons that exist NOW —
  // a lazily expanded empty same-key field adds one, a paste removes one.
  // It used to be written once at copy time and then go stale both ways.
  {
    const PDATA = {
      qubits: {
        qA1: { __class__: 'q.Transmon', id: 'qA1', confusion_matrix: [[0.98, 0.02], [0.03, 0.97]] },
        qA2: { __class__: 'q.Transmon', id: 'qA2', confusion_matrix: [] }
      }
    };
    const win = makeWorld(function (url) {
      if (url === '/field/edit') return jsonResp({ ok: true });
      return jsonResp({ ok: true, values: {}, expected: {} });
    }, null, PDATA);
    const c = win.document.getElementById('tree');
    const toggleOf = function (p) {
      return c.querySelector('.tree-node[data-path="' + p + '"] > .tree-row .tree-toggle.collapsed');
    };
    const t1 = toggleOf('qubits.qA1');
    ok(!!t1, 'JT-22: qA1 starts collapsed (the fixture reaches the lazy path)');
    if (t1) t1.click();
    ok(!nodeAt(c, 'qubits.qA2.confusion_matrix'),
       'JT-22: qA2 is not materialised yet (no paste target exists at copy time)');
    const src = nodeAt(c, 'qubits.qA1.confusion_matrix');
    src.querySelector(':scope > .tree-row .tree-key')
       .dispatchEvent(new win.MouseEvent('dblclick', { bubbles: true }));
    const pill = win.document.getElementById('tree-copy-pill');
    const label = function () { return pill ? pill.textContent : ''; };
    ok(pill && !pill.hidden && /open an empty 'confusion_matrix' to paste/.test(label()),
       'JT-22: with no empty target in view the pill says to open one (' + label() + ')');
    ok(win.document.documentElement.classList.contains('tree-copy-active'),
       'JT-22 x F6: the pill showing gives the tree its bottom room (html.tree-copy-active)');
    const t2 = toggleOf('qubits.qA2');
    if (t2) t2.click();
    await tick();
    const tgt = nodeAt(c, 'qubits.qA2.confusion_matrix');
    const btn = tgt && tgt.querySelector(':scope > .tree-row .tree-paste-btn');
    ok(!!btn, 'JT-22: expanding qA2 materialises its empty confusion_matrix with a paste button');
    ok(/click “paste” on 1 empty field\b/.test(label()) && !/open an empty/.test(label()),
       'JT-22: the pill now counts the new paste button (' + label() + ')');
    if (btn) btn.click();
    await tick(25);
    ok(!win.document.querySelector('.tree-paste-btn'), 'JT-22: the pasted field drops its button');
    ok(/open an empty 'confusion_matrix' to paste/.test(label()),
       'JT-22: after the paste the pill no longer claims a field to paste into (' + label() + ')');
    ok(!pill.hidden, 'JT-22: the buffer survives the paste (paste into many)');
    win.document.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(pill.hidden, 'JT-22: Escape still clears the copy');
    ok(!win.document.documentElement.classList.contains('tree-copy-active'),
       'JT-22 x F6: ...and takes the bottom room back');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('explorer_crud_selfcheck: all checks passed');
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
