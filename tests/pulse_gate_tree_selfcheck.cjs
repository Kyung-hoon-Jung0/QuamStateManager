// w9/pulsegate (user decision 2026-09-28): a pulse is added, deleted, renamed
// or copied ONLY on the Pulses page. The REAL app.js under jsdom:
//   G1 a pulse object (any operations entry, a pair gate slot holding
//      something, a row the Pulses page found by shape) and an operations
//      dict offer no ＋/✕ -- a hover-revealed "Pulses are added, removed and
//      renamed on the Pulses page" with a link to /pulses/goto?path=<it>;
//      a field inside a pulse, a channel, a gate, a null slot, anything under
//      extras keep their ＋/✕ exactly as before
//   G2 without the server's payload (a dataset tree, a harness) the tree has
//      no opinion -- the old buttons
//   G3 the link navigates IN the app (htmx GET into #table-pane; the server
//      answers HX-Location); a modifier click keeps the browser's own way
//   G4 the write doors' refusals carry the way on: a structural refusal ->
//      "Open the Pulses page"; a lab "Delete together" whose set holds a
//      pulse -> a link to the same offer there (no batch button); a set
//      without a pulse -> the tree's own batch button, as before
//   G5 the add-key panel and the JSON editor show the link on a refusal
//   G6 TAKE LIVE (live-diff ✓ both kinds, Accept all, the sync review ✓)
//      says so: source "live" (the server verifies it)
// Run: node tests/pulse_gate_tree_selfcheck.cjs  (driven by tests/test_pulse_structure.py)
'use strict';

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const APP_JS = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const SQ = 'quam.components.pulses.SquarePulse';
const DATA = {
  qubits: { q1: {
    __class__: 'q.Transmon',
    xy: { __class__: 'q.IQChannel', intermediate_frequency: 1e8,
          operations: { x180: { __class__: SQ, amplitude: 0.1, length: 40 },
                        alias: '#./x180' } } } },
  qubit_pairs: { 'q1-2': { macros: { cz: {
    __class__: 'lab.CZGate', flux_pulse_qubit: '#/qubits/q1/xy/operations/x180',
    coupler_flux_pulse: null, phase: 0.1,
    spect: { q3: { __class__: SQ, amplitude: 0.01, length: 8 } } } } } },
  extras: { operations: { z: 1 } }
};
const PAYLOAD = {
  skip_tops: ['extras', 'network', 'ports', 'wiring'],
  gate_slots: ['flux_pulse_qubit', 'coupler_flux_pulse', 'flux_pulse_target'],
  rows: ['qubit_pairs.q1-2.macros.cz.spect.q3'], rows_known: true, goto: '/pulses/goto'
};
const NOTE = 'Pulses are added, removed and renamed on the Pulses page';

function jsonResp(obj, status) {
  return Promise.resolve({ ok: (status || 200) < 400, status: status || 200,
    json: function () { return Promise.resolve(obj); },
    text: function () { return Promise.resolve(JSON.stringify(obj)); } });
}

function makeWorld(fetchImpl, payload) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane"><div id="tree"></div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/explorer' });
  const win = dom.window;
  win._fetchCalls = [];
  win.fetch = function (url, opts) {
    win._fetchCalls.push({ url: url, opts: opts || {} });
    return fetchImpl(url, opts || {});
  };
  new win.Function(APP_JS).call(win);
  win._ajax = [];
  win.htmx = { ajax: function (m, u, o) { win._ajax.push({ m: m, u: u, o: o }); return Promise.resolve(); },
               process: function () {}, trigger: function () {} };
  if (payload) win._treePulseGate = JSON.parse(JSON.stringify(payload));
  win.renderJsonTree('tree', JSON.parse(JSON.stringify(DATA)), { defaultDepth: 1, crud: true });
  const c = win.document.getElementById('tree');
  for (let round = 0; round < 10; round++) {
    const t = c.querySelectorAll('.tree-toggle.collapsed');
    if (!t.length) break;
    t.forEach(function (x) { x.click(); });
  }
  return win;
}
function nodeAt(win, p) {
  return win.document.querySelector('.tree-node[data-path="' + p + '"]');
}
function hover(win, p) {
  const node = nodeAt(win, p);
  const row = node.querySelector(':scope > .tree-row');
  row.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  return node;
}
function acts(win, p) {
  const node = hover(win, p);
  const span = node.querySelector(':scope > .tree-row > .tree-row-actions');
  return {
    add: !!(span && span.querySelector('.tree-act-add')),
    del: !!(span && span.querySelector('.tree-act-del')),
    type: !!(span && span.querySelector('.tree-act-type')),
    note: span && span.querySelector('.tree-pulse-gate'),
    node: node
  };
}
const quiet = function (url) {
  if (url.indexOf('/field/refs') === 0) return jsonResp({ ok: true, total: 0, refs: [] });
  if (url.indexOf('/schema/missing-keys') === 0) return jsonResp({ ok: true, warm: false, missing: [] });
  return jsonResp({ ok: true, values: {}, expected: {} });
};

(async function main() {
  // G1 --------------------------------------------------------------------
  {
    const win = makeWorld(quiet, PAYLOAD);
    const guarded = [
      ['qubits.q1.xy.operations.x180', 'an operations entry (a pulse dict)'],
      ['qubits.q1.xy.operations.alias', 'an operations entry (an alias)'],
      ['qubits.q1.xy.operations', 'an operations dict'],
      ['qubit_pairs.q1-2.macros.cz.flux_pulse_qubit', 'a pair gate slot holding a link'],
      ['qubit_pairs.q1-2.macros.cz.spect.q3', 'a pulse the Pulses page found by shape']
    ];
    guarded.forEach(function (g) {
      const a = acts(win, g[0]);
      ok(!a.add && !a.del, 'G1: no ＋/✕ on ' + g[1]);
      ok(!!a.note && a.note.textContent === NOTE, 'G1: the note on ' + g[1]);
      const link = a.note && a.note.querySelector('a.tree-pulses-link');
      ok(!!link && link.getAttribute('href') === '/pulses/goto?path=' + encodeURIComponent(g[0]),
         'G1: the link opens the Pulses page on ' + g[0] + ' (' + (link && link.getAttribute('href')) + ')');
    });
    ok(acts(win, 'qubits.q1.xy.operations.alias').type, 'G1: a leaf pulse keeps its type picker (not structural)');
    const open = [
      ['qubits.q1.xy.operations.x180.amplitude', true, false, 'a field inside a pulse'],
      ['qubits.q1.xy', true, true, 'a channel'],
      ['qubit_pairs.q1-2.macros.cz', true, true, 'a gate'],
      ['qubit_pairs.q1-2.macros.cz.coupler_flux_pulse', true, false, 'an empty (null) gate slot'],
      ['qubit_pairs.q1-2.macros.cz.spect', true, true, 'a plain dict inside a gate'],
      ['extras.operations.z', true, false, 'anything under extras'],
      ['extras.operations', true, true, 'an operations dict under extras']
    ];
    open.forEach(function (o) {
      const a = acts(win, o[0]);
      ok(a.del === o[1] && a.add === o[2] && !a.note, 'G1: ' + o[3] + ' keeps its buttons, no note');
    });
  }
  // G2 --------------------------------------------------------------------
  {
    const win = makeWorld(quiet, null);
    const a = acts(win, 'qubits.q1.xy.operations.x180');
    ok(a.add && a.del && !a.note, 'G2: no payload -- the old buttons (no opinion)');
    const b = acts(win, 'qubits.q1.xy.operations');
    ok(b.add && b.del && !b.note, 'G2: ...on an operations dict too');
  }
  // G3 --------------------------------------------------------------------
  {
    const win = makeWorld(quiet, PAYLOAD);
    const a = acts(win, 'qubits.q1.xy.operations.x180');
    const link = a.note.querySelector('a');
    let bubbled = 0;
    a.node.addEventListener('click', function () { bubbled++; });
    const ev = new win.MouseEvent('click', { bubbles: true, cancelable: true });
    link.dispatchEvent(ev);
    const call = win._ajax[0];
    ok(!!call && call.m === 'GET' && call.u === '/pulses/goto?path=qubits.q1.xy.operations.x180'
       && call.o && call.o.target === '#table-pane', 'G3: a click goes through htmx into #table-pane');
    ok(ev.defaultPrevented && bubbled === 0, 'G3: ...not a page load, and the row does not also react');
    const ev2 = new win.MouseEvent('click', { bubbles: true, cancelable: true, ctrlKey: true });
    link.dispatchEvent(ev2);
    ok(win._ajax.length === 1 && !ev2.defaultPrevented, 'G3: Ctrl+click keeps the browser\'s own way (a new tab)');
  }
  // G4 --------------------------------------------------------------------
  async function refuse(answer, onPath) {
    const win = makeWorld(function (url) {
      if (url === '/field/delete') return jsonResp(answer, answer.error_kind ? 409 : 400);
      return quiet(url);
    }, PAYLOAD);
    const node = hover(win, onPath);
    node.querySelector(':scope > .tree-row .tree-act-del').click();
    await tick(20);
    node.querySelectorAll(':scope > .tree-row .tree-row-actions .tree-act-btn')[0].click();
    await tick(30);
    return node.querySelector(':scope > .tree-row > .tree-edit-err');
  }
  {
    const chip = await refuse({ ok: false, error_kind: 'pulse_structure',
      error: 'x is a pulse. ' + NOTE, pulses_page: '/pulses/goto?path=x' }, 'qubits.q1.xy');
    const l = chip && chip.querySelector('a.tree-pulses-link');
    ok(!!l && l.textContent === 'Open the Pulses page' && l.getAttribute('href') === '/pulses/goto?path=x',
       'G4: a structural refusal names the way on');
  }
  {
    const URL = '/pulses/goto?path=qubits.q1.xy.operations.x180&together=qubit_pairs.q1-2.macros.cz';
    const chip = await refuse({ ok: false, lab_refused: true, error: 'the gate plays it by name',
      lab_delete_also: ['qubits.q1.xy.operations.x180'], lab_delete_label: 'Delete together with 1 op',
      lab_delete_pulses_url: URL }, 'qubit_pairs.q1-2.macros.cz');
    const l = chip && chip.querySelector('a.tree-pulses-link');
    ok(!!l && l.textContent === 'Delete together with 1 op on the Pulses page' && l.getAttribute('href') === URL,
       'G4: a "Delete together" holding a pulse is a link to the same offer on the Pulses page');
    ok(chip && !chip.querySelector('.tree-cascade-btn'), 'G4: ...and never the tree\'s own batch');
  }
  {
    const chip = await refuse({ ok: false, lab_refused: true, error: 'required field',
      lab_delete_also: ['qubit_pairs.q1-2.macros.cz'], lab_delete_label: 'Delete together with 1 gate' },
      'qubit_pairs.q1-2.macros.cz.phase');
    ok(chip && !!chip.querySelector('.tree-cascade-btn') && !chip.querySelector('a.tree-pulses-link'),
       'G4: a set without a pulse stays the tree\'s own batch, as before');
  }
  // G5 --------------------------------------------------------------------
  {
    const win = makeWorld(function (url) {
      if (url === '/field/create') return jsonResp({ ok: false, error_kind: 'pulse_structure',
        error: 'no', pulses_page: '/pulses/goto?path=a' }, 409);
      return quiet(url);
    }, PAYLOAD);
    const node = hover(win, 'qubits.q1.xy');
    node.querySelector(':scope > .tree-row .tree-act-add').click();
    const panel = node.querySelector(':scope > .tree-crud-panel');
    panel.querySelector('.tree-crud-key').value = 'k';
    panel.querySelector('.tree-crud-ok').click();
    await tick(30);
    const l = panel.querySelector('.tree-crud-err a.tree-pulses-link');
    ok(!!l && l.getAttribute('href') === '/pulses/goto?path=a', 'G5: the add-key panel shows the link');
  }
  {
    const win = makeWorld(function (url) {
      if (url === '/field/edit') return jsonResp({ ok: false, error_kind: 'pulse_structure',
        error: 'this edit would remove x90', pulses_page: '/pulses/goto?path=b' }, 409);
      return quiet(url);
    }, PAYLOAD);
    const node = nodeAt(win, 'qubits.q1.xy.operations');
    node.querySelector(':scope > .tree-row > .tree-json-edit-btn').click();
    const ta = node.querySelector('.tree-json-textarea');
    ta.value = '{"x180": {}}';
    node.querySelector('.tree-json-editor-bar button').click();
    await tick(30);
    const err = node.querySelector('.tree-json-err');
    ok(!err.hidden && /remove x90/.test(err.textContent) && !!err.querySelector('a.tree-pulses-link'),
       'G5: the JSON editor shows the refusal and the link');
  }
  // G6 --------------------------------------------------------------------
  {
    const win = makeWorld(function () { return jsonResp({ ok: true, results: [{ applied: true }] }); }, PAYLOAD);
    const d = win.document;
    const node = d.createElement('div'); node.className = 'tree-node';
    const row = d.createElement('div'); row.className = 'tree-row';
    const val = d.createElement('span'); val.className = 'tree-val'; row.appendChild(val);
    node.appendChild(row); d.body.appendChild(node);
    win._ldReviewButtons(row, { dot_path: 'qubits.q1.xy.operations.y90', value: { __class__: SQ }, op: 'create' }, node);
    row.querySelector('.tree-accept-btn').click();
    const row2 = row.cloneNode(false); row2.appendChild(d.createElement('span')).className = 'tree-val';
    node.appendChild(row2);
    win._ldReviewButtons(row2, { dot_path: 'qubits.q1.f_01', value: 5 }, node);
    row2.querySelector('.tree-accept-btn').click();
    await tick(20);
    const posts = win._fetchCalls.filter(function (x) { return x.url === '/field/edit-batch'; })
      .map(function (x) { return JSON.parse(x.opts.body); });
    ok(posts.length === 2 && posts.every(function (b) { return b.source === 'live'; }),
       'G6: the live-diff ✓ (structural and leaf) says it is taking live');
    // the sync review's ✓
    const wrap = d.createElement('div'); wrap.className = 'review-live-edit';
    const inp = d.createElement('input'); inp.className = 'review-live-input';
    inp.setAttribute('data-dot-path', 'qubits.q1.xy.operations.alias'); inp.value = '#./x180';
    const btn = d.createElement('button'); wrap.appendChild(inp); wrap.appendChild(btn);
    d.body.appendChild(wrap);
    win.reviewAccept(btn);
    await tick(20);
    const last = win._fetchCalls[win._fetchCalls.length - 1];
    ok(last.url === '/field/edit-batch' && JSON.parse(last.opts.body).source === 'live',
       'G6: the sync review ✓ says it is taking live');
  }
  {
    // Accept all: the page-level function reads the rows of a live diff
    const src = APP_JS;
    const i = src.indexOf('updates: updates, independent: true');
    ok(i > 0 && /source: "live"/.test(src.slice(i, i + 80)),
       'G6: Accept all says it is taking live');
  }

  if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
  console.log('ALL OK');
  process.exit(0);   // app.js leaves timers running
})().catch(function (e) { console.error(e); process.exit(1); });
