// Behavioral check for All-values v2 (the REAL all-values.js under jsdom):
//  - resolvable xref rows render as edit-through inputs; focus fetches
//    /field/peek ONCE and shows the "writes to … · shared by …" hint
//  - dangling xref rows stay read-only (raw pointer text, no input)
//  - array rows carry a ✎ JSON modal that posts the PARSED value (never a
//    string) to /field/edit-batch; bad JSON + server errors render inline
//  - expected-type chips (extra.ty) render inside the 18px value band
//  - structural 28px parity: every leaf row is one <tr><td> with exactly the
//    3 grid children; hint + modal never enter the table flow
//  - dirty-preserving applyPayload rebase carries extra through a re-pull
//
// Run: node tests/all_values_v2_selfcheck.cjs   (needs jsdom)
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
const AV_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'all-values.js'), 'utf8');

// Minimal All-values pane: segmented control + panes + the scroller skeleton
// (wireDom guards every optional toolbar node, so only the core ids matter).
const DOM = `
  <button type="button" class="bulk-seg" data-pane="grid">Grid</button>
  <button type="button" class="bulk-seg" data-pane="allvalues">All values</button>
  <div data-bulk-pane="grid"></div>
  <div data-bulk-pane="allvalues" hidden>
    <input type="search" id="av-search">
    <span id="av-coverage"></span><span id="av-showing"></span>
    <span id="av-dirty-count"></span>
    <button id="av-apply" disabled></button><button id="av-reset" disabled></button>
    <div id="av-chips"></div>
    <div class="av-scroll" id="av-scroll">
      <table class="av-table-virtual" id="av-table">
        <tbody id="av-tbody"></tbody>
      </table>
    </div>
  </div>`;

const XREF = 'qubits.qA1.z.opx_output';
const DANGLING = 'qubits.qA1.pump_ref';
const MATRIX = 'qubits.qA1.confusion_matrix';

function freshRows() {
  // deep-fresh every call: applyPayload takes ownership and mutates rows
  return [
    ['qubits.qA1.f_01', '6,250,000,000', 'scalar', 0, { ty: { t: 'number', s: 'inferred' } }],
    [XREF, '0.1', 'xref', 0, { p: '#/wiring/qubits/qA1/z/opx_output', d: 0, ty: { t: 'number', s: 'env' } }],
    [DANGLING, '#/wiring/twpas/tA/pump', 'xref', 0, { p: '#/wiring/twpas/tA/pump', d: 1 }],
    [MATRIX, '[2×2]', 'array', 0, { dims: '2×2' }],
    [MATRIX + '.0', '[2]', 'array', 0],
    [MATRIX + '.0.0', '0.98', 'list', 0],
    ['qubits.qA1.extras', '{} empty', 'empty', 0]
  ];
}
// QA F17: the server counts what the client edits -- the scalar, the live xref
// and the list element are editable (3); only the dangling xref is read-only.
const SUMMARY = { total: 4, editable: 3, readonly: 1, by_kind: {}, arrays: 2, empties: 1 };

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.__chipToken = 'tok';
  // jsdom has no layout: give the scroller a viewport so the virtual window
  // paints every display item of this small fixture.
  Object.defineProperty(win.document.getElementById('av-scroll'), 'clientHeight',
    { value: 600 });

  win._log = { peek: {}, editBatch: [] };
  win._editBatchQueue = [];       // shift()ed per POST; empty → generic ok
  win._payloadRows = freshRows;   // swappable for the rebase step
  let etagN = 0;
  function jsonResp(data, headers) {
    headers = headers || {};
    return Promise.resolve({
      status: 200,
      headers: { get: function (k) { return headers[k] || null; } },
      json: function () { return Promise.resolve(data); }
    });
  }
  win.fetch = function (url, opts) {
    if (url.indexOf('/bulk/all-values') === 0) {
      return jsonResp({ rows: win._payloadRows(), summary: SUMMARY },
        { ETag: '"e-' + (++etagN) + '"' });
    }
    if (url.indexOf('/field/peek') === 0) {
      const p = decodeURIComponent(url.split('dot_path=')[1]);
      win._log.peek[p] = (win._log.peek[p] || 0) + 1;
      const values = {}; values[MATRIX] = [[0.98, 0.02], [0.03, 0.97]];
      const resolved = {};
      resolved[XREF] = { resolved_path: 'ports.analog_outputs.con1.5.offset',
        shared_by: ['qubits.qA1.z.alias', 'qubits.qA2.z.alias'] };
      return jsonResp({ ok: true, values: values, errors: {}, resolved: resolved });
    }
    if (url === '/field/edit-batch') {
      const body = JSON.parse(opts.body);
      win._log.editBatch.push(body);
      const r = win._editBatchQueue.length ? win._editBatchQueue.shift()
        : { ok: true, results: body.updates.map(function (u) {
            return { dot_path: u.dot_path, resolved_path: u.dot_path,
              applied: true, new_value: u.value, display: String(u.value) };
          }) };
      return jsonResp(r);
    }
    return Promise.reject(new Error('unexpected fetch ' + url));
  };
  new win.Function(AV_JS).call(win);
  return win;
}

function rowsIn(win) {
  return Array.prototype.filter.call(
    win.document.querySelectorAll('#av-tbody tr'),
    function (tr) { return !tr.classList.contains('av-spacer'); });
}
function expandGroup(win) {
  const grow = win.document.querySelector('#av-tbody .av-group-row');
  grow.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
}
function inputFor(win, p) {
  return win.document.querySelector('#av-tbody .av-input[data-dot-path="' + p + '"]');
}

(async function main() {
  const win = makeWorld();
  win.AllValues.switchPane('allvalues');
  await tick();
  expandGroup(win);

  // V1: xref editable vs dangling read-only + list-element input
  {
    const xin = inputFor(win, XREF);
    ok(!!xin, 'V1: resolvable xref renders an .av-input');
    ok(xin && xin.value === '0.1', 'V1: xref input holds the RESOLVED value');
    ok(!inputFor(win, DANGLING), 'V1: dangling xref renders NO input');
    const dtr = rowsIn(win).filter(function (tr) {
      return tr.textContent.indexOf(DANGLING) >= 0;
    })[0];
    ok(dtr && dtr.textContent.indexOf('#/wiring/twpas/tA/pump') >= 0,
      'V1: dangling xref shows the raw pointer text');
    ok(!!inputFor(win, MATRIX + '.0.0'), 'V1: list element renders an input');
  }

  // V2: type chips inside the value band
  {
    const chips = win.document.querySelectorAll('#av-tbody .av-ty-chip');
    ok(chips.length === 2, 'V2: two ty chips (scalar + xref), got ' + chips.length);
    const texts = Array.prototype.map.call(chips, function (c) { return c.textContent; });
    ok(texts.indexOf('number·inf') >= 0, 'V2: inferred source abbreviates to ·inf');
    ok(texts.indexOf('number·env') >= 0, 'V2: env chip renders on the xref row');
    const scalarChip = Array.prototype.filter.call(chips, function (c) {
      return c.title.indexOf('inferred') >= 0;
    })[0];
    ok(!!scalarChip, 'V2: chip title carries the full source');
  }

  // V3: structural 28px parity — one td, exactly 3 grid children, no block elems
  {
    rowsIn(win).forEach(function (tr) {
      ok(tr.children.length === 1, 'V3: row has one td (' + tr.textContent + ')');
      const td = tr.children[0];
      ok(td.children.length === 3,
        'V3: td has exactly 3 grid children, got ' + td.children.length
        + ' (' + td.textContent + ')');
      ok(!td.querySelector('div, br, table'),
        'V3: no block-level element inside a row');
    });
  }

  // V4: focus on the xref input peeks ONCE and paints the write-through hint
  {
    const xin = inputFor(win, XREF);
    xin.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
    await tick();
    const hint = win.document.getElementById('av-xref-hint');
    ok(!!hint, 'V4: hint element appears on focus');
    ok(hint && hint.textContent ===
      'writes to ports.analog_outputs.con1.5.offset · shared by qubits.qA1.z.alias, qubits.qA2.z.alias',
      'V4: hint text = resolved path + shared_by, got: ' + (hint && hint.textContent));
    ok(!win.document.querySelector('#av-tbody #av-xref-hint') &&
       !win.document.querySelector('#av-table #av-xref-hint'),
      'V4: hint never enters the table flow');
    xin.dispatchEvent(new win.FocusEvent('focusout', { bubbles: true }));
    ok(!win.document.getElementById('av-xref-hint'), 'V4: hint hides on blur');
    xin.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
    await tick();
    ok(win._log.peek[XREF] === 1, 'V4: peek fetched once per path (cached), got '
      + win._log.peek[XREF]);
    xin.dispatchEvent(new win.FocusEvent('focusout', { bubbles: true }));
  }

  // V5: array ✎ modal — prefill from peek, inline errors, PARSED-value POST
  {
    const btn = win.document.querySelector('[data-av-edit="' + MATRIX + '"]');
    ok(!!btn, 'V5: array row carries the ✎ button');
    btn.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    await tick();
    const modal = win.document.getElementById('av-json-modal');
    ok(!!modal, 'V5: modal opens');
    ok(!win.document.querySelector('#av-table #av-json-modal'),
      'V5: modal never enters the table flow');
    const ta = modal.querySelector('.av-modal-ta');
    ok(ta.value === JSON.stringify([[0.98, 0.02], [0.03, 0.97]], null, 2),
      'V5: textarea prefilled with pretty JSON of the RAW value');
    // bad JSON → inline error, no POST
    ta.value = 'not json';
    ta.dispatchEvent(new win.KeyboardEvent('keydown',
      { key: 'Enter', ctrlKey: true, bubbles: true }));
    ok(!modal.querySelector('.av-modal-err').hidden, 'V5: invalid JSON errors inline');
    ok(win._log.editBatch.length === 0, 'V5: invalid JSON never POSTs');
    // server rejection → inline error, modal stays
    win._editBatchQueue.push({ ok: false, results: [{ dot_path: MATRIX, applied: false, error: 'boom' }] });
    ta.value = '[[1, 2], [3, 4]]';
    ta.dispatchEvent(new win.KeyboardEvent('keydown',
      { key: 'Enter', metaKey: true, bubbles: true }));
    await tick();
    ok(win.document.getElementById('av-json-modal') === modal, 'V5: modal stays on server error');
    ok(modal.querySelector('.av-modal-err').textContent === 'boom',
      'V5: server error renders inline');
    // success → PARSED array in the JSON body (never a string), modal closes
    ta.dispatchEvent(new win.KeyboardEvent('keydown',
      { key: 'Enter', ctrlKey: true, bubbles: true }));
    await tick();
    const post = win._log.editBatch[1];
    ok(post && Array.isArray(post.updates[0].value)
      && JSON.stringify(post.updates[0].value) === '[[1,2],[3,4]]',
      'V5: POST carries the PARSED value');
    ok(post && post.expect_chip === 'tok', 'V5: chip token stamped');
    ok(!win.document.getElementById('av-json-modal'), 'V5: modal closes on success');
    // QA liveedit-r2-21: focus goes back to this path's ✎, never to <body>
    // (the row repaints on save, so the ✎ is found again by its path)
    const aeS = win.document.activeElement;
    ok(!!aeS && aeS.getAttribute && aeS.getAttribute('data-av-edit') === MATRIX,
      'V5: Save returns focus to the row ✎ (got ' + (aeS && aeS.tagName) + ')');
    ok(win.AllValues._state.etag === null, 'V5: etag dropped → next activation re-pulls');
    const mrow = win.AllValues._state.rows[win.AllValues._state.rowsByPath.get(MATRIX)];
    ok(mrow[1] === '[2×2]' && mrow[3] === 1 && mrow[4].dims === '2×2',
      'V5: row display/dims mirrored locally + modified-flagged');
    // Esc closes without saving
    const btn2 = win.document.querySelector('[data-av-edit="qubits.qA1.extras"]');
    ok(!!btn2, 'V5: empty-container row carries the ✎ button too');
    btn2.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    await tick();
    win.document.getElementById('av-json-modal')
      .dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(!win.document.getElementById('av-json-modal'), 'V5: Esc cancels');
    const aeE = win.document.activeElement;
    ok(!!aeE && aeE.getAttribute && aeE.getAttribute('data-av-edit') === 'qubits.qA1.extras',
      'V5: Esc returns focus to the ✎ that opened it (got ' + (aeE && aeE.tagName) + ')');
    ok(win._log.editBatch.length === 2, 'V5: Esc never POSTs');
  }

  // V6: dirty rebase across a re-pull carries extra through + keeps the edit
  {
    const st = win.AllValues._state;
    const xin = inputFor(win, XREF);
    xin.value = '0.25';
    xin.dispatchEvent(new win.Event('input', { bubbles: true }));
    ok(st.dirty.has(XREF), 'V6: typed xref edit is dirty');
    // out-of-band change: the server now resolves the xref to 0.15
    win._payloadRows = function () {
      const rows = freshRows();
      rows[1][1] = '0.15';
      return rows;
    };
    st.etag = null;
    win.AllValues.switchPane('allvalues');   // re-activation → dirty-preserving re-pull
    await tick();
    const d = st.dirty.get(XREF);
    ok(!!d && d.value === '0.25', 'V6: dirty edit survives the re-pull');
    ok(d && d.orig === '0.15', 'V6: dirty baseline rebased onto the fresh value');
    const row = st.rows[st.rowsByPath.get(XREF)];
    ok(row[4] && row[4].p === '#/wiring/qubits/qA1/z/opx_output' && row[4].d === 0,
      'V6: extra carried through the rebase');
    expandGroup(win);
    const xin2 = inputFor(win, XREF);
    ok(xin2 && xin2.value === '0.25', 'V6: repainted input shows the kept edit');
    const f01 = st.rows[st.rowsByPath.get('qubits.qA1.f_01')];
    ok(f01[4] && f01[4].ty && f01[4].ty.t === 'number',
      'V6: ty extras present after the rebase');
  }

  // V7 (QA liveedit-r2-01): Escape CANCELS a Flat View edit -- Table View
  // parity (docs/120 item 9). Before, the typed value stayed dirty and the next
  // click-away (focusout commits) POSTed the value the user had abandoned.
  {
    const st = win.AllValues._state;
    const F01 = 'qubits.qA1.f_01';
    if (!inputFor(win, F01)) expandGroup(win);   // V6 left it expanded
    const fin = inputFor(win, F01);
    const stored = st.rows[st.rowsByPath.get(F01)][1];
    fin.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
    fin.value = '7.7e-05';
    fin.dispatchEvent(new win.Event('input', { bubbles: true }));
    const tr = fin.closest('tr');
    ok(st.dirty.has(F01) && tr.classList.contains('av-row-dirty')
       && win.document.getElementById('av-dirty-count').textContent !== '',
      'V7: fixture -- the typed edit is dirty and counted');
    const esc = new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    fin.dispatchEvent(esc);
    ok(fin.value === stored, 'V7: Escape restores the stored value, got ' + fin.value);
    ok(!st.dirty.has(F01) && !tr.classList.contains('av-row-dirty'),
      'V7: Escape un-dirties the row');
    ok(esc.defaultPrevented, 'V7: the Escape is consumed (no popover/inspector close too)');
    const posts = win._log.editBatch.length;
    fin.dispatchEvent(new win.FocusEvent('focusout', { bubbles: true }));
    await tick(20);
    ok(win._log.editBatch.length === posts,
      'V7: the click-away after Escape commits nothing (' + (win._log.editBatch.length - posts) + ' POST)');
    // with nothing typed, Escape is left to the app (not swallowed)
    const esc2 = new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    fin.dispatchEvent(esc2);
    ok(!esc2.defaultPrevented, 'V7: a clean Escape passes through to the app');
  }

  // V8 (QA liveedit-r2-29): an unknown `is:` qualifier matched EVERY leaf, so
  // a typo (is:modifed) read "Showing 7 of 7" -- as if every leaf were
  // modified. It matches nothing now and the count line names it.
  {
    const box = win.document.getElementById('av-search');
    const showing = () => win.document.getElementById('av-showing').textContent;
    async function search(q) {
      box.value = q;
      box.dispatchEvent(new win.Event('input', { bubbles: true }));
      await tick(150);   // past the 80 ms debounce
    }
    const total = win.AllValues._state.rows.length;
    await search('is:modifed');
    ok(/^Showing 0 of /.test(showing()), 'V8: a typo qualifier matches nothing, got ' + showing());
    ok(/unknown qualifier is:modifed/.test(showing()) && /is:modified/.test(showing()),
      'V8: and the count line names the unknown qualifier + the known ones, got ' + showing());
    await search('is:modified');
    const nMod = parseInt(showing().replace(/^Showing ([\d,]+).*/, '$1').replace(/,/g, ''), 10);
    ok(nMod > 0 && nMod < total && !/unknown/.test(showing()),
      'V8: a known qualifier still filters, no hint, got ' + showing());
    // the app's one grammar: with SearchQuery loaded, `|` ORs -- an unknown
    // qualifier used to swallow the whole group (every row)
    new win.Function(fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static',
      'search-query.js'), 'utf8')).call(win);
    await search('is:bogus | f_01');
    ok(/^Showing 1 of /.test(showing()) && /unknown qualifier is:bogus/.test(showing()),
      'V8: is:bogus | f_01 shows the f_01 leaf only (+ the hint), got ' + showing());
    await search('');
    ok(showing() === '', 'V8: clearing the box clears the line');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('all_values_v2_selfcheck: all checks passed');
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
