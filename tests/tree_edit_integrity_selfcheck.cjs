// jsdom selfcheck: what the Json tree SHOWS after an edit agrees with what the
// server holds (QA pass 2026-09-24, jsontree chunk 2).
//  - jsontree-r2-07: a pasted dict shared ONE object with its source, so a
//    later source edit showed up in the copy and the copy's ✎ Save wrote it
//  - jsontree-r2-08: an array echoed into a leaf row read as '1,2'
//  - jsontree-r2-10: 'Apply FSP + compensate N amplitudes' repainted only the
//    FSP row (with the TYPED text); the amplitude rows kept the old values
//  - jsontree-r2-17: Exit diff with an edit box open re-fetched the tree
//    BEFORE the blur-commit landed -- the screen contradicted the tray
//  - JT-07: pending tints came only from the commit sites -- a re-render, a
//    lazily built row or a redo showed an unapplied value untinted
//
// Run: node tests/tree_edit_integrity_selfcheck.cjs   (driven by
// tests/test_undo_trail.py::test_tree_edit_integrity_selfcheck)
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
function jsonResp(obj, delay) {
  const r = { ok: true, status: 200, json: function () { return Promise.resolve(obj); } };
  return delay ? new Promise(function (res) { setTimeout(function () { res(r); }, delay); }) : Promise.resolve(r);
}
function trayHtml(paths) {
  return '<div id="pending-tray" data-change-count="' + paths.length + '" data-change-sig="s' + paths.join('|') + '">'
    + paths.map(function (p) {
      return '<div class="tray-change-item"><code class="tray-change-path" title="' + p + '">' + p + '</code></div>';
    }).join('') + '</div>';
}

// A world whose /field/edit echoes the committed value and a tray listing
// every path written so far (the server change log). `win._handlers[url]`
// overrides a route for one test.
function makeWorld(data, opts) {
  opts = opts || {};
  const tray = opts.tray === null ? '' : trayHtml(opts.tray || []);
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + tray
    + '<div id="table-pane"><div id="explorer-tree-state" class="json-tree"></div>'
    + '<div id="explorer-tree-wiring" style="display:none"></div></div>'
    + '</body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/explorer' });
  const win = dom.window;
  win._log = (opts.tray || []).slice();
  win._server = {};
  win._calls = [];
  win._handlers = {};
  win.fetch = function (url, opts2) {
    url = String(url);
    const body = opts2 && opts2.body ? String(opts2.body) : '';
    win._calls.push({ url: url, body: body });
    for (const k of Object.keys(win._handlers)) {
      if (url.indexOf(k) === 0) return win._handlers[k](url, body);
    }
    if (url.indexOf('/field/peek') === 0) {
      const dp = decodeURIComponent(url.split('dot_path=')[1] || '');
      const values = {};
      values[dp] = Object.prototype.hasOwnProperty.call(win._server, dp) ? win._server[dp] : null;
      return jsonResp({ ok: true, values: values, errors: {}, expected: {} });
    }
    if (url.indexOf('/field/edit') === 0) {
      const b = new win.URLSearchParams(body);
      const dp = b.get('dot_path');
      let v = b.get('value');
      try { v = JSON.parse(v); } catch (e) { /* text */ }
      win._server[dp] = v;
      if (win._log.indexOf(dp) < 0) win._log.push(dp);
      return jsonResp({ ok: true, stored: v, stored_kind: typeof v, tray_html: trayHtml(win._log) });
    }
    if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
    return new Promise(function () {});
  };
  win._ajax = [];
  win.htmx = {
    ajax: function (m, u) { win._ajax.push(m + ' ' + u); return Promise.resolve(); },
    trigger: function () {}, process: function () {}
  };
  win.navigator.clipboard = { writeText: function () { return Promise.resolve(); } };
  new win.Function(APP_JS).call(win);
  win.renderJsonTree('explorer-tree-state', JSON.parse(JSON.stringify(data)), { defaultDepth: 1, crud: true });
  return win;
}

function tree(win) { return win.document.getElementById('explorer-tree-state'); }
function nodeAt(win, p) { return tree(win).querySelector('.tree-node[data-path="' + p + '"]'); }
function open(win, p) {
  const n = nodeAt(win, p);
  const t = n && n.querySelector(':scope > .tree-row > .tree-toggle.collapsed');
  if (t) t.click();
  return n;
}
function openAll(win, p) {
  const segs = p.split('.');
  for (let i = 1; i <= segs.length; i++) open(win, segs.slice(0, i).join('.'));
  return nodeAt(win, p);
}
function valText(win, p) {
  const n = nodeAt(win, p);
  const v = n && n.querySelector(':scope > .tree-row > .tree-val');
  return v ? v.textContent : null;
}
function isPending(win, p) {
  const n = nodeAt(win, p);
  const r = n && n.querySelector(':scope > .tree-row');
  return !!(r && r.classList.contains('tree-row-pending'));
}
function model(win, p) {
  let cur = tree(win)._treeData;
  for (const s of p.split('.')) { if (cur == null) return undefined; cur = cur[s]; }
  return cur;
}
// click a leaf, type, and commit with Enter (or leave it open with `blurOnly`)
function typeInto(win, p, text, how) {
  const v = nodeAt(win, p).querySelector(':scope > .tree-row > .tree-val');
  v.click();
  const input = v.querySelector('input');
  input.value = text;
  if (how === 'blur') input.dispatchEvent(new win.FocusEvent('blur'));
  else if (how !== 'open') input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  return input;
}

(async function main() {
  // ── jsontree-r2-07: a paste is a copy, not an alias ──────────────────────
  {
    const DATA = { qubits: {
      q1: { extras: { T1_vs_flux_best_flux_V: -0.12, epg: 0.01 } },
      q2: { resonator: { extras: {} } },
      q3: { resonator: { extras: {} } } } };
    const win = makeWorld(DATA);
    openAll(win, 'qubits.q1'); openAll(win, 'qubits.q2.resonator'); openAll(win, 'qubits.q3.resonator');
    const key = nodeAt(win, 'qubits.q1.extras').querySelector(':scope > .tree-row > .tree-key');
    key.dispatchEvent(new win.MouseEvent('dblclick', { bubbles: true }));
    const btn = nodeAt(win, 'qubits.q3.resonator.extras').querySelector('.tree-paste-btn');
    ok(!!btn, 'fixture: the empty extras offers ⎘ paste');
    btn.click();
    await tick(30);
    ok(model(win, 'qubits.q3.resonator.extras.T1_vs_flux_best_flux_V') === -0.12, 'fixture: the paste landed');

    // edit the SOURCE after the paste
    openAll(win, 'qubits.q1.extras');
    typeInto(win, 'qubits.q1.extras.T1_vs_flux_best_flux_V', '-0.88');
    await tick(30);
    ok(model(win, 'qubits.q1.extras.T1_vs_flux_best_flux_V') === -0.88, 'fixture: the source edit reached the model');
    ok(model(win, 'qubits.q3.resonator.extras.T1_vs_flux_best_flux_V') === -0.12,
      'r2-07: the pasted copy keeps its paste-time value in the model');
    ok(nodeAt(win, 'qubits.q3.resonator.extras')._value !== nodeAt(win, 'qubits.q1.extras')._value,
      'r2-07: the copy node does not share the source node\'s object');
    open(win, 'qubits.q3.resonator.extras');
    ok(valText(win, 'qubits.q3.resonator.extras.T1_vs_flux_best_flux_V') === '-0.12',
      'r2-07: expanding the copy shows the paste-time value (got ' +
      valText(win, 'qubits.q3.resonator.extras.T1_vs_flux_best_flux_V') + ')');
    nodeAt(win, 'qubits.q3.resonator.extras').querySelector(':scope > .tree-row > .tree-json-edit-btn').click();
    const ta = nodeAt(win, 'qubits.q3.resonator.extras').querySelector('.tree-json-textarea');
    ok(ta && JSON.parse(ta.value).T1_vs_flux_best_flux_V === -0.12,
      'r2-07: the copy\'s ✎ editor opens with the paste-time value, not the source\'s new one');

    // copy -> edit the source -> paste: the buffer is the COPY-time value,
    // and the model gets exactly what the server was sent
    typeInto(win, 'qubits.q1.extras.epg', '0.5');
    await tick(30);
    const b2 = nodeAt(win, 'qubits.q2.resonator.extras').querySelector('.tree-paste-btn');
    b2.click();
    await tick(30);
    const sent = win._calls.filter(function (c) { return c.body.indexOf('qubits.q2.resonator.extras') >= 0; }).pop();
    const sentVal = JSON.parse(new win.URLSearchParams(sent.body).get('value'));
    ok(sentVal.epg === 0.01, 'r2-07: a paste carries the value as COPIED (epg ' + sentVal.epg + ')');
    ok(JSON.stringify(model(win, 'qubits.q2.resonator.extras')) === JSON.stringify(sentVal),
      'r2-07: the pasted model equals what the server was sent');
    ok(model(win, 'qubits.q2.resonator.extras') !== model(win, 'qubits.q3.resonator.extras'),
      'r2-07: two pastes are two objects');
  }

  // ── jsontree-r2-08: an array in a leaf row reads as JSON ─────────────────
  {
    const win = makeWorld({ qubits: { q2: { cm: [[0.903, null]] } } });
    ok(win._treeFormatValue([1, 2]) === '[1,2]', 'r2-08: _formatValue([1,2]) is its JSON literal, not "1,2"');
    ok(win._treeFormatValue({ a: 1 }) === '{"a":1}', 'r2-08: an object reads as JSON, not [object Object]');
    ok(win._treeFormatValue(1234.5) === win._treeFormatValue(1234.5) && win._treeFormatValue(null) === 'null',
      'r2-08: scalars are unchanged');
    openAll(win, 'qubits.q2.cm.0');
    typeInto(win, 'qubits.q2.cm.0.1', '[1,2]');
    await tick(30);
    ok(valText(win, 'qubits.q2.cm.0.1') === '[1,2]', 'r2-08: the echoed list row reads [1,2] (got ' +
      valText(win, 'qubits.q2.cm.0.1') + ')');
  }

  // ── jsontree-r2-10: the FSP bundle repaints every row it wrote ───────────
  {
    const FSP = 'ports.mw_outputs.con1.3.2.full_scale_power_dbm';
    const A1 = 'qubits.q1.xy.operations.x180_DragCosine.amplitude';
    const A2 = 'qubits.q1.xy.operations.x90_DragCosine.amplitude';
    const DATA = {
      ports: { mw_outputs: { con1: { '3': { '2': { full_scale_power_dbm: 2 } } } } },
      qubits: { q1: { xy: { operations: {
        x180_DragCosine: { amplitude: 0.30458920811564044 },
        x90_DragCosine: { amplitude: 0.15229460405782022 } } } } } };
    const plan = { amps: [{ path: A1, old: 0.30458920811564044, new: 0.271465 },
                          { path: A2, old: 0.15229460405782022, new: 0.1357325 }] };
    const win = makeWorld(DATA);
    win._openFspPopup = function (p, cb) { cb('comp', p); };
    let batchReply = null;
    win._handlers['/field/edit-batch'] = function () { return jsonResp(batchReply); };
    win._handlers['/field/edit'] = function (url, body) {
      const b = new win.URLSearchParams(body);
      if (b.get('dot_path') === FSP) return jsonResp({ ok: false, fsp_compensation: plan });
      return jsonResp({ ok: true, stored: 0, stored_kind: 'number' });
    };
    openAll(win, 'ports.mw_outputs.con1.3.2');
    openAll(win, 'qubits.q1.xy.operations.x180_DragCosine');   // A1 built; A2's branch never built
    batchReply = { ok: true, tray_html: trayHtml([FSP, A1, A2]), results: [
      { dot_path: FSP, resolved_path: FSP, applied: true, new_value: 3, display: '3' },
      { dot_path: A1, resolved_path: A1, applied: true, new_value: 0.271465, display: '0.271465' },
      { dot_path: A2, resolved_path: A2, applied: true, new_value: 0.1357325, display: '0.1357325' }] };
    typeInto(win, FSP, '3');
    await tick(40);
    ok(win._calls.some(function (c) { return c.url === '/field/edit-batch'; }), 'fixture: comp posted the bundle');
    ok(valText(win, A1) === win._treeFormatValue(0.271465),
      'r2-10: the built amplitude row shows the compensated value (got ' + valText(win, A1) + ')');
    ok(model(win, A1) === 0.271465, 'r2-10: the model holds the compensated amplitude');
    ok(isPending(win, A1), 'r2-10: the compensated row is tinted as unapplied');
    ok(model(win, A2) === 0.1357325, 'r2-10: a never-built branch\'s model is updated too');
    openAll(win, 'qubits.q1.xy.operations.x90_DragCosine');
    ok(valText(win, A2) === win._treeFormatValue(0.1357325), 'r2-10: expanding it shows the compensated value');
    ok(model(win, FSP) === 3 && typeof model(win, FSP) === 'number',
      'r2-10: the FSP model is the committed NUMBER, not the typed string');
    ok(valText(win, FSP) === '3', 'r2-10: the FSP row reads 3');

    // a refused bundle names the row's reason instead of "edit rejected"
    batchReply = { ok: false, tray_html: trayHtml([FSP, A1, A2]), results: [
      { dot_path: FSP, applied: false, error: 'rolled back due to other failure(s) in this batch' },
      { dot_path: A1, applied: false, error: 'Type mismatch at ' + A1 + ': expected real' }] };
    typeInto(win, FSP, '4');
    await tick(40);
    const err = nodeAt(win, FSP).querySelector('.tree-edit-err');
    ok(err && err.textContent.indexOf('expected real') >= 0 && err.textContent.indexOf(A1) >= 0,
      'r2-10: a refused bundle shows the server\'s row error (got ' + (err && err.textContent) + ')');
  }

  // ── jsontree-r2-17: a re-fetch waits for the open edit to land ───────────
  {
    const win = makeWorld({ qubits: { q5: { T2ramsey: 0.000026301346984159078 } } });
    const order = [];
    win._handlers['/field/edit'] = function (url, body) {
      order.push('POST');
      return jsonResp({ ok: true, stored: 0.000044, stored_kind: 'number', tray_html: trayHtml(['qubits.q5.T2ramsey']) }, 40)
        .then(function (r) { order.push('RESOLVED'); return r; });
    };
    win.htmx.ajax = function (m, u) { order.push('GET ' + u); return Promise.resolve(); };
    openAll(win, 'qubits.q5');
    typeInto(win, 'qubits.q5.T2ramsey', '4.4e-05', 'blur');   // blur: commit is 100 ms deferred
    win.explorerLiveDiff(false);                              // Exit diff, at once
    await tick(200);
    ok(order.indexOf('POST') >= 0, 'fixture: the typed value was committed');
    ok(order.indexOf('GET /explorer') > order.indexOf('RESOLVED') && order.indexOf('RESOLVED') >= 0,
      'r2-17: the /explorer re-fetch waits for the commit to land (' + order.join(' > ') + ')');
    ok(order.filter(function (x) { return x === 'POST'; }).length === 1, 'r2-17: the blur timer does not post twice');
    ok(order.filter(function (x) { return x === 'GET /explorer'; }).length === 1, 'r2-17: exactly one re-fetch');

    order.length = 0;
    win.explorerLiveDiff(false);
    ok(order[0] === 'GET /explorer', 'r2-17: with no edit open the re-fetch stays synchronous');

    // an unchanged editor is no reason to wait
    order.length = 0;
    const v = nodeAt(win, 'qubits.q5.T2ramsey').querySelector(':scope > .tree-row > .tree-val');
    v.click();
    win._softRefreshLiveSurface();
    ok(order[0] === 'GET /explorer' && order.indexOf('POST') < 0,
      'r2-17: an open-but-unchanged editor is cancelled, nothing posted, no wait');
  }

  // ── JT-07: pending tints follow the tray ──────────────────────────────────
  {
    const P = 'qubits.q3.anharmonicity';
    const DATA = { ver: 3, qubits: { q2: { T2echo: 1e-5 }, q3: { anharmonicity: 210000000, f_01: 5e9 } } };
    const win = makeWorld(DATA, { tray: [P, 'ver'] });
    // the server tray lands through the htmx lane (UndoQueue / declarative
    // swaps) or through _swapPendingTray (every JS edit caller)
    function htmxTray(w, paths) {
      w.document.getElementById('pending-tray').outerHTML = trayHtml(paths);
      const t = w.document.getElementById('pending-tray');
      t.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: t } }));
    }
    function redo(w, p) {
      w.document.dispatchEvent(new w.CustomEvent('cellsReverted', {
        detail: { message: 'Redone', entries: [{ dot_path: p, old_value_str: '210000000', old_value_disp: '210000000' }] } }));
    }
    ok(isPending(win, 'ver'), 'JT-07: a first render tints a row the tray lists');
    openAll(win, 'qubits.q3');
    ok(isPending(win, P), 'JT-07: a row built on expand is tinted from the tray');
    ok(!isPending(win, 'qubits.q3.f_01'), 'JT-07: a row the tray does not list is not');

    // a fresh render (reload / soft refresh / live diff) keeps it
    win.renderJsonTree('explorer-tree-state', JSON.parse(JSON.stringify(DATA)), { defaultDepth: 1, crud: true });
    ok(isPending(win, 'ver'), 'JT-07: a re-render reads the tray, the tint survives');
    openAll(win, 'qubits.q3');
    ok(isPending(win, P), 'JT-07: ...and on the re-rendered lazy rows');

    // redo: the repaint strips the tint, the tray that lands with it restores it
    win._server[P] = 210000000;
    redo(win, P);
    ok(!isPending(win, P), 'fixture: the redo repaint stripped the tint');
    htmxTray(win, [P, 'ver']);
    ok(isPending(win, P), 'JT-07: the htmx tray swap that still lists it puts the tint back');

    // the JS lane: an ordinary inline edit's own tray swap (_swapPendingTray)
    redo(win, P);
    openAll(win, 'qubits.q2');
    win._log = [P, 'ver'];
    typeInto(win, 'qubits.q2.T2echo', '2e-05');
    await tick(30);
    ok(isPending(win, 'qubits.q2.T2echo'), 'fixture: the edited row is tinted');
    ok(isPending(win, P), 'JT-07: an edit\'s tray swap (_swapPendingTray) restores the stripped tint too');

    // a tray that no longer lists it retires it
    htmxTray(win, ['qubits.q2.T2echo']);
    ok(!isPending(win, P) && !isPending(win, 'ver'), 'JT-07: a tray that no longer lists the path removes the tint');
    ok(isPending(win, 'qubits.q2.T2echo'), 'JT-07: ...and keeps the one it does list');

    // no tray at all: a render neither throws nor tints
    const win2 = makeWorld(DATA, { tray: null });
    openAll(win2, 'qubits.q3');
    ok(!isPending(win2, P) && !isPending(win2, 'ver'), 'JT-07: with no tray nothing is tinted (and nothing throws)');

    // an ancestor keeps a tint it already has (create-key tints the parent)
    const win3 = makeWorld(DATA, { tray: ['qubits.q3.new_key'] });
    openAll(win3, 'qubits.q3');
    nodeAt(win3, 'qubits.q3').querySelector(':scope > .tree-row').classList.add('tree-row-pending');
    htmxTray(win3, ['qubits.q3.new_key']);
    ok(isPending(win3, 'qubits.q3'), 'JT-07: the parent of a created key keeps its tint');
  }

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('all tree edit-integrity checks passed');
})().catch(function (e) { console.error(e); process.exit(1); });
