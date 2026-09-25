// jsontree-r2-19: the Json Tree View's ⚠ row marks and the sidebar
// diagnostics dots follow edits and acknowledgements WITHOUT a reload.
//
// They used to be redone only on a #table-pane htmx:afterSwap or a full page
// load, so an inline edit (which fires 'diagnostics-changed') left a ⚠ reading
// "(got 30)" beside a value of 32, and an acknowledgement followed by the
// sidebar round trip (PaneState keep-alive restore -> 'paneRestored', never an
// afterSwap) brought the old ⚠ and the red dot back.
//
// Runs the REAL shipped app.js under jsdom:
//   M1  an edit that CREATES a finding marks the row + lights the dot
//   M2  an edit that FIXES it clears both
//   M3  an acknowledgement (paneRestored) clears an error's red dot + ⚠
//   M4  the re-mark does not reopen a folded subtree (expand:false)
//   M5  a late findings.json reply cannot repaint over a newer one
//
// Run: node tests/diag_marks_live_selfcheck.cjs   (needs jsdom; exit 2 = skip)
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
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 8); }); }

const TOF = 'qubits.q1.resonator.time_of_flight';
const DATA = {
  qubits: {
    q1: { T1: 2e-5, resonator: { time_of_flight: 30, depletion_time: 400 } },
    q2: { T1: 3e-5, f_01: 5e9 }
  }
};

function finding(jp, sev, ack) {
  return { jump_path: jp, severity: sev || 'warning', acknowledged: !!ack,
           message: 'time_of_flight must be a multiple of 4 (got 30)' };
}

(async function main() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>' +
    '<nav id="sidebar"><a href="/explorer">Json Tree View</a><a href="/instrument">Wiring</a></nav>' +
    '<div id="table-pane"><div id="explorer-tree-state"></div><div id="explorer-tree-wiring"></div></div>' +
    '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/explorer' });
  const win = dom.window;

  // findings.json is served from `win._findings`; `win._delay[n]` holds the
  // n-th findings.json reply back for that many ms (M5).
  win._findings = { value_spec: [], connectivity: [] };
  win._delay = {};
  win._findingsReads = 0;
  win.fetch = function (url) {
    if (String(url).indexOf('/diagnostics/findings.json') === 0) {
      const n = ++win._findingsReads;
      const body = JSON.parse(JSON.stringify(win._findings));
      const resp = { ok: true, status: 200, json: function () { return Promise.resolve(body); } };
      const d = win._delay[n];
      return d ? new Promise(function (r) { setTimeout(function () { r(resp); }, d); })
               : Promise.resolve(resp);
    }
    return Promise.resolve({ ok: true, status: 200,
      json: function () { return Promise.resolve({}); },
      text: function () { return Promise.resolve(''); } });
  };
  new win.Function(APP_JS).call(win);

  const doc = win.document;
  win.renderJsonTree('explorer-tree-state', JSON.parse(JSON.stringify(DATA)), { defaultDepth: 1 });
  const c = doc.getElementById('explorer-tree-state');
  // open q1 -> resonator so the edited row is on screen (as it is when edited)
  function open(p) {
    const n = c.querySelector('.tree-node[data-path="' + p + '"]');
    const t = n && n.querySelector(':scope > .tree-row > .tree-toggle');
    if (t && t.classList.contains('collapsed')) t.click();
  }
  open('qubits'); open('qubits.q1'); open('qubits.q1.resonator');
  await tick(50);   // let the on-load findings read settle

  function rowOf(p) {
    const n = c.querySelector('.tree-node[data-path="' + p + '"]');
    return n && n.querySelector(':scope > .tree-row');
  }
  const nav = doc.querySelector('#sidebar a[href="/explorer"]');
  function fire(name) {
    // htmx.trigger(document.body, name) dispatches a bubbling CustomEvent;
    // PaneState dispatches paneRestored on document.
    const tgt = name === 'paneRestored' ? doc : doc.body;
    tgt.dispatchEvent(new win.CustomEvent(name, { bubbles: true, detail: { route: '/explorer' } }));
  }

  ok(!!rowOf(TOF), 'setup: the edited row is rendered');
  ok(!rowOf(TOF).querySelector('.tree-warn-icon'), 'setup: no ⚠ before the edit');

  // M1: edit to 30 creates the finding
  win._findings = { value_spec: [finding(TOF)], connectivity: [] };
  fire('diagnostics-changed');
  await tick(520);
  ok(!!rowOf(TOF).querySelector('.tree-warn-icon'), 'M1: the edit that creates a finding marks the row');
  ok(nav.classList.contains('nav-diag-dot-warn'), 'M1: ...and lights the amber sidebar dot');

  // M2: edit to 32 fixes it
  win._findings = { value_spec: [], connectivity: [] };
  fire('diagnostics-changed');
  await tick(520);
  ok(!rowOf(TOF).querySelector('.tree-warn-icon'), 'M2: the fixing edit clears the ⚠ (no "(got 30)" left)');
  ok(!nav.classList.contains('nav-diag-dot-warn') && !nav.classList.contains('nav-diag-dot'),
     'M2: ...and the sidebar dot');

  // M3: an error finding, then acknowledged + keep-alive restore
  win._findings = { value_spec: [finding(TOF, 'error')], connectivity: [] };
  fire('diagnostics-changed');
  await tick(520);
  ok(nav.classList.contains('nav-diag-dot'), 'M3: an error lights the red dot');
  win._findings = { value_spec: [finding(TOF, 'error', true)], connectivity: [] };
  fire('paneRestored');
  await tick(520);
  ok(!nav.classList.contains('nav-diag-dot'), 'M3: acknowledged + pane restore clears the red dot');
  ok(!rowOf(TOF).querySelector('.tree-warn-icon'), 'M3: ...and the ⚠ on the restored tree');

  // M3b (r2-19 review): a keep-alive restore of ANY OTHER route has no marks
  // to repaint and reads nothing (the sidebar dots follow 'diagnostics-changed')
  const pane = doc.getElementById('table-pane');
  const parked = doc.createElement('div');
  while (pane.firstChild) parked.appendChild(pane.firstChild);
  pane.innerHTML = '<div id="bulk-stub">bulk</div>';
  const readsBefore = win._findingsReads;
  doc.dispatchEvent(new win.CustomEvent('paneRestored', { bubbles: true, detail: { route: '/bulk' } }));
  await tick(520);
  ok(win._findingsReads === readsBefore,
     'M3b: a restore of /bulk issues no findings.json read (' + (win._findingsReads - readsBefore) + ')');
  pane.innerHTML = '';
  while (parked.firstChild) pane.appendChild(parked.firstChild);

  // M4: a finding under the FOLDED q2 does not reopen it
  const q2 = c.querySelector('.tree-node[data-path="qubits.q2"]');
  const q2t = q2.querySelector(':scope > .tree-row > .tree-toggle');
  ok(q2t.classList.contains('collapsed'), 'M4: setup -- q2 is folded');
  win._findings = { value_spec: [finding('qubits.q2.T1')], connectivity: [] };
  fire('diagnostics-changed');
  await tick(520);
  ok(q2t.classList.contains('collapsed'), 'M4: the re-mark after an edit leaves the folded q2 folded');
  ok(!c.querySelector('.tree-node[data-path="qubits.q2.T1"]'),
     'M4: ...and materialises nothing under it');

  // M5: an older reply that lands late loses to the newer one
  win._findings = { value_spec: [finding(TOF)], connectivity: [] };
  win._delay[win._findingsReads + 1] = 200;           // the first read is slow
  win._applyExplorerSpecMarks({ expand: false });
  await tick(5);
  win._findings = { value_spec: [], connectivity: [] };
  win._applyExplorerSpecMarks({ expand: false });      // the newer read is fast
  await tick(320);
  ok(!rowOf(TOF).querySelector('.tree-warn-icon'),
     'M5: a late, older findings reply does not repaint the stale ⚠');

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('diag_marks_live_selfcheck: all checks passed');
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
