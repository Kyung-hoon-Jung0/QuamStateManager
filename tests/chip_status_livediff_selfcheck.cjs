/* QA chipstatus-r2-02 — the "changed vs live" marks come down once live matches.
 *
 * Measured on the customer chip copy: edit q2 T1 + a q1-2 IRB, open Chip
 * Status, press "⚡ Apply to live now". The toast said applied, the badge said
 * Synced, GET /state/live-diff returned 0 entries -- and 21 elements kept the
 * .topo-changed outline and the '… changed vs live' tooltip line 3 s and 11 s
 * later. liveDiff.refresh ran only at mount and in the live banner's show;
 * decorate() only ever APPENDED its tooltip line (and never updated its count).
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  L1  mount marks what /state/live-diff names, keeping the element's own title;
 *  L2  liveDriftChanged (what every apply / pull / Take live fires) re-reads it,
 *      and the tooltip count is the NEW count, on one line, not a second one;
 *  L3  a live-diff with 0 entries clears every outline AND every tooltip line,
 *      leaving the element's own title as it was (and no empty title behind);
 *  L4  a slow response from an earlier read never lands over a later one;
 *  L5  stateRestored (fired on document) re-reads too;
 *  L6  a transient 503 (live mid-write) keeps the marks and asks once more;
 *  L7  the live poll going changed -> unchanged re-reads (a mid-apply read
 *      that cached entries is not left on screen).
 *
 * Run: node tests/chip_status_livediff_selfcheck.cjs
 *      (driven by tests/test_chip_status_live_marks.py)
 */
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
  + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
  + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
  + '<div id="probe">'
  + '<span id="p-q2" data-qubit="q2" title="q2 · T1 24.0 µs">q2</span>'
  + '<span id="p-q12" data-pair="q1-2">q1-2</span>'
  + '<span id="p-q3" data-qubit="q3" title="q3">q3</span>'
  + '</div>'
  + '</div></div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=overview' });
const win = dom.window;
win.htmx = { process: function () {}, ajax: function () {} };

function entries(list) { return { ok: true, total: list.length, entries: list.map(function (p) { return { dot_path: p }; }) }; }
let diffAnswer = entries(['qubits.q2.T1', 'qubits.q2.T2ramsey', 'qubit_pairs.q1-2.macros.cz_SNZ.fidelity.IRB']);
let diffCalls = 0;
let holdNext = null;          // when set, the NEXT live-diff answer waits for release()
let mtimeAnswer = { changed: false, state_mtime: 1, wiring_mtime: 1 };
function reply(body, status) {
  return { ok: status === 200, status: status, json: function () { return Promise.resolve(body); } };
}
win.fetch = function (url) {
  const p = String(url).split('?')[0];
  if (p === '/state/live-diff') {
    diffCalls++;
    const body = JSON.parse(JSON.stringify(diffAnswer));
    const status = body.transient ? 503 : 200;
    if (holdNext) {
      const h = holdNext; holdNext = null;
      return new Promise(function (res) { h.release = function () { res(reply(body, status)); }; });
    }
    return Promise.resolve(reply(body, status));
  }
  if (p === '/api/topology-mtime') return Promise.resolve(reply(JSON.parse(JSON.stringify(mtimeAnswer)), 200));
  return new Promise(function () {});   // everything else: hold, never race the asserts
};
new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);

const doc = win.document;
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function marks() { return doc.querySelectorAll('.topo-changed').length; }
function lined() {
  return Array.prototype.filter.call(doc.querySelectorAll('[title]'),
    function (e) { return /changed vs live/.test(e.getAttribute('title')); }).length;
}
function title(id) { return doc.getElementById(id).getAttribute('title'); }
function moved(target) {
  (target || doc.body).dispatchEvent(new win.CustomEvent(
    target === doc ? 'stateRestored' : 'liveDriftChanged', { bubbles: true }));
}

win.ChipStatus.mount({ topo: { nodes: [], edges: [] }, rawWiring: {}, diagFindings: [],
                       metricMeta: {}, defaultThresholds: {} });

(async function () {
  await wait(50);
  /* L1 */
  ok(doc.getElementById('p-q2').classList.contains('topo-changed')
     && doc.getElementById('p-q12').classList.contains('topo-changed')
     && !doc.getElementById('p-q3').classList.contains('topo-changed'),
    'L1a: mount marks q2 and q1-2, not q3 — ' + marks() + ' marked');
  ok(title('p-q2') === 'q2 · T1 24.0 µs\n2 field(s) changed vs live — "Review changes" shows before/after',
    'L1b: the line joins the element\'s own title — ' + JSON.stringify(title('p-q2')));

  /* L2: another field changed; an apply-ish event re-reads */
  diffAnswer = entries(['qubits.q2.T1', 'qubits.q2.T2ramsey', 'qubits.q2.T2echo',
                        'qubit_pairs.q1-2.macros.cz_SNZ.fidelity.IRB']);
  let before = diffCalls;
  moved();
  await wait(450);
  ok(diffCalls === before + 1, 'L2a: liveDriftChanged re-reads /state/live-diff — ' + (diffCalls - before) + ' read(s)');
  ok(title('p-q2') === 'q2 · T1 24.0 µs\n3 field(s) changed vs live — "Review changes" shows before/after',
    'L2b: the count is the new one, on one line — ' + JSON.stringify(title('p-q2')));

  /* L3: the apply landed; live matches */
  diffAnswer = entries([]);
  moved();
  await wait(450);
  ok(marks() === 0, 'L3a: no outline once live matches — ' + marks());
  ok(lined() === 0, 'L3b: no "changed vs live" tooltip line left — ' + lined());
  ok(title('p-q2') === 'q2 · T1 24.0 µs', 'L3c: the element\'s own title is untouched — ' + JSON.stringify(title('p-q2')));
  ok(!doc.getElementById('p-q12').hasAttribute('title'),
    'L3d: an element that had no title has none again — ' + JSON.stringify(title('p-q12')));

  /* L4: an earlier read answers LAST (the mid-apply read in the report) */
  diffAnswer = entries(['qubits.q2.T1']);
  const held = {};
  holdNext = held;
  win.ChipStatus.liveDiff.refresh();            // read #1, answer held (2 entries)
  diffAnswer = entries([]);
  win.ChipStatus.liveDiff.refresh();            // read #2, answers now (0 entries)
  await wait(30);
  held.release();                               // #1 lands late
  await wait(30);
  ok(marks() === 0 && lined() === 0, 'L4: a late earlier answer never lands over a later one — '
     + marks() + ' marked, ' + lined() + ' lined');

  /* L5: a restore (fired on document) re-reads */
  diffAnswer = entries(['qubits.q3.T1']);
  before = diffCalls;
  moved(doc);
  await wait(450);
  ok(diffCalls === before + 1 && doc.getElementById('p-q3').classList.contains('topo-changed'),
    'L5: stateRestored re-reads and marks q3 — ' + (diffCalls - before) + ' read(s)');

  /* L6: live mid-write answers 503 transient: marks stay, one retry */
  diffAnswer = { ok: false, transient: true, error: 'Live chip is being written' };
  before = diffCalls;
  win.ChipStatus.liveDiff.refresh();
  await wait(50);
  ok(doc.getElementById('p-q3').classList.contains('topo-changed'),
    'L6a: a transient 503 keeps the marks it had');
  diffAnswer = entries([]);
  await wait(1700);
  ok(diffCalls === before + 2 && marks() === 0,
    'L6b: …and asks once more, which clears them — ' + (diffCalls - before) + ' reads, ' + marks() + ' marked');

  /* L7: the live poll sees changed (mid-apply), then unchanged (it settled) */
  diffAnswer = entries(['qubits.q2.T1']);        // what a mid-apply read sees
  mtimeAnswer = { changed: true, state_mtime: 5, wiring_mtime: 1 };
  win.ChipStatus.liveDetection();               // polls now (changed) -> banner in 2 s
  await wait(2400);
  ok(marks() === 1, 'L7a: the banner\'s mid-apply read marked q2 — ' + marks());
  mtimeAnswer = { changed: false, state_mtime: 6, wiring_mtime: 1 };
  diffAnswer = entries([]);
  before = diffCalls;
  await wait(1000);                             // the next 3 s poll: unchanged
  ok(diffCalls === before + 1 && marks() === 0,
    'L7b: changed -> unchanged re-reads and clears the marks — ' + (diffCalls - before) + ' read(s), '
    + marks() + ' marked');
  clearInterval(win.ChipStatus._livePollTimer);

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_livediff_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
