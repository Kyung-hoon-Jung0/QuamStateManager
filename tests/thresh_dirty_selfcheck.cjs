/* docs/192 CS02 — a threshold typed but not applied must SAY so.
 *
 * Commit in this editor is EXPLICIT: the "Update colour bands" button, or Enter
 * in a field. That is a fair choice. What was not fair is that a typed-and-not-
 * applied number looked exactly like a saved one, under a hint that described
 * the SAVED state. Measured in real Chrome before the fix:
 *
 *   type 70, press Tab  ->  the box reads 70
 *                           the hint still read "your lab's bands … shared with
 *                           everyone using this SM"
 *                           the server still held 60
 *                           only a reload revealed it
 *
 * These bands decide the in-spec verdict for everyone using this SM, so an
 * uncommitted one has to be visible (docs/120: a press means what the presser
 * could see).
 *
 * Run: node tests/thresh_dirty_selfcheck.cjs   (needs jsdom)
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

// The shape ChipStatus.mount really takes is `nodes`/`edges` (a thin
// qubits/pairs fixture makes mount bail early and the threshold functions —
// which live inside mount — never become global at all).
const topo = {
  nodes: [
    { id: 'qA1', grid_location: '0,0', f_01: 6.10e9, T1: 2.4e-5,
      gate_fidelity_avg: 0.9990, metrics: { T1: { value: 2.4e-5 } } },
    { id: 'qA2', grid_location: '1,0', f_01: 5.80e9, T1: 1.9e-5,
      gate_fidelity_avg: 0.9984, metrics: { T1: { value: 1.9e-5 } } },
  ],
  edges: [],
  summary: {},
};

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div class="topo-dashboard">'
  + '<div id="topo-hero"></div><div id="topo-thresh-editor" hidden></div>'
  + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
win.htmx = { ajax: function () {} };
// The editor POSTs its bands; hold every request so nothing races the asserts.
const posts = [];
win.fetch = function (url, opts) {
  posts.push({ url: String(url), body: opts && opts.body, method: opts && opts.method });
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true }); } });
};
new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                 + read('chip-status.js')).call(win);
// The editor builds one row per threshold it KNOWS, so an empty
// defaultThresholds gives an empty editor and every pin below would be vacuous
// (docs/141 §4af). These are the real shapes, as `/chip-status/spec` returns them.
win.ChipStatus.mount({
  topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
  defaultThresholds: {
    gate_fidelity_avg: { direction: 'higher', warn: 0.99, fail: 0.95, label: '1Q gate fidelity' },
    T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' },
  },
});

const doc = win.document;
win.toggleThresholdEditor();

const host = doc.getElementById('topo-thresh-editor');
const inputs = host.querySelectorAll('.thresh-in');
ok(inputs.length > 0, 'A1: the editor builds its fields — ' + inputs.length);

const inp = inputs[0];
ok(inp.getAttribute('data-saved') !== null,
  'A2: every field carries the value the server holds, so "dirty" is a comparison');
ok(inp.getAttribute('data-saved') === inp.value,
  'A3: …and at rest the two agree');
ok(!inp.classList.contains('thresh-dirty'), 'A4: nothing is marked at rest');

const status = doc.getElementById('thresh-status');
const restedHint = status ? status.textContent : '';
ok(!/NOT applied/i.test(restedHint), 'A5: and the hint does not claim an edit at rest');

/* ── typed, not applied ─────────────────────────────────────────────── */
inp.value = String(parseFloat(inp.getAttribute('data-saved')) + 10);
inp.dispatchEvent(new win.Event('input', { bubbles: true }));

ok(inp.classList.contains('thresh-dirty'),
  'B1: a typed value the server has not been told about is marked');
ok(/NOT applied/i.test(status.textContent),
  'B2: …and the hint says so instead of describing the saved state — '
  + status.textContent.slice(0, 60));
ok(/Update colour bands/i.test(status.textContent),
  'B3: …naming the control that would commit it');
// Count the THRESHOLD door only: app.js polls /state/live-diff in the
// background, and "any fetch happened" is not the question being asked.
// WRITES only (QA chipstatus-r2-06): opening the editor now re-reads the
// server's bands with a GET, which sends nothing.
const specPosts = function () {
  return posts.filter(function (p) {
    return /chip-status\/spec/.test(p.url) && p.method === 'POST';
  });
};
ok(specPosts().length === 0, 'B4: and nothing was sent — the commit really is explicit');

/* ── typed back to the saved value ──────────────────────────────────── */
inp.value = inp.getAttribute('data-saved');
inp.dispatchEvent(new win.Event('input', { bubbles: true }));
ok(!inp.classList.contains('thresh-dirty'),
  'C1: typing back to the saved value clears the mark (a comparison, not a flag)');
ok(!/NOT applied/i.test(status.textContent), 'C2: …and the hint goes back');

/* ── applying ───────────────────────────────────────────────────────── */
inp.value = String(parseFloat(inp.getAttribute('data-saved')) + 10);
inp.dispatchEvent(new win.Event('input', { bubbles: true }));
ok(inp.classList.contains('thresh-dirty'), 'D0: dirty again, ready to apply');
win.applyThresholds();

const after = doc.getElementById('topo-thresh-editor').querySelectorAll('.thresh-in')[0];
ok(after.getAttribute('data-saved') === after.value,
  'D1: applying makes the saved value the typed one');
ok(!after.classList.contains('thresh-dirty'), 'D2: …and clears the mark');
ok(specPosts().length > 0,
  'D3: …and actually sends it to the threshold door — '
  + (specPosts()[0] && specPosts()[0].url));

/* A second field must be judged on its own, not on its neighbour's state. */
if (inputs.length > 1) {
  const host2 = doc.getElementById('topo-thresh-editor');
  const two = host2.querySelectorAll('.thresh-in');
  two[1].value = String(parseFloat(two[1].getAttribute('data-saved')) + 1);
  two[1].dispatchEvent(new win.Event('input', { bubbles: true }));
  ok(two[1].classList.contains('thresh-dirty') && !two[0].classList.contains('thresh-dirty'),
    'E1: one edited field does not mark its neighbours');
  ok(/^1 threshold is/.test(doc.getElementById('thresh-status').textContent),
    'E2: …and the count is the number really edited — '
    + doc.getElementById('thresh-status').textContent.slice(0, 40));
}

console.log(fails ? ('FAILED (' + fails + ')')
  : ('thresh_dirty_selfcheck ok (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
