/* jsdom selfcheck for the JSON tree's DIFF behaviour (docs/84).
 *
 * The tree already had a diff overlay — it is what the Explorer's "Live diff"
 * uses — but it could only ever show a value CHANGE on a key both sides have:
 * it iterated the primary document's keys, so a key only one side carried
 * rendered as nothing at all. An IDE-style diff must show added and removed.
 *
 * It also printed its own delta with toFixed(6)/toExponential(3), so the same
 * change read "(+0.000123)" here and "+100,000,000 (+1.96%)" in the Review
 * tray. docs/76 says there is ONE delta implementation.
 *
 * Covers: union rendering (added / removed / changed), the removed subtree
 * still expanding, ValueDelta as the only delta, the read-only "diff" click
 * mode, and — the regression that matters — live-diff behaving exactly as it
 * did before union mode existed.
 *
 * Run: NODE_PATH=<node_modules> node tests/diff_tree_selfcheck.cjs
 * (driven by tests/test_diff_tree_client.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="t-union"></div><div id="t-live"></div><div id="t-plain"></div>' +
    '</body></html>',
    { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

window.fetch = function () { return new Promise(function () {}); };
window.htmx = { ajax: function () {}, on: function () {}, trigger: function () {},
                process: function () {} };
window.Plotly = { newPlot: function () { return Promise.resolve(); },
                  react: function () { return Promise.resolve(); }, purge: function () {} };

const staticDir = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
window.eval(fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8'));

ok(typeof window.renderJsonTree === 'function', 'real app.js loaded (renderJsonTree present)');
ok(typeof window.ValueDelta === 'object', 'ValueDelta present (the one delta implementation)');

/* ══ 1. union mode: changed / added / removed ═══════════════════════════ */
const A = { qubits: { qA1: { T1: 1.0e-5, gone: 7 } } };
const B = { qubits: { qA1: { T1: 3.0e-5, fresh: 9 } } };
window.renderJsonTree('t-union', A, { refData: B, union: true, valueClick: 'diff',
                                      defaultDepth: 99 });
const u = document.getElementById('t-union');
function nodeAt(root, p) { return root.querySelector('[data-path="' + p + '"]'); }

const changed = nodeAt(u, 'qubits.qA1.T1');
ok(!!changed && changed.classList.contains('tree-diff'), 'a changed leaf is marked');
ok(!!changed && changed.textContent.indexOf('→') !== -1,
   'a changed leaf shows the other side: ' + JSON.stringify((changed || {}).textContent));

const added = nodeAt(u, 'qubits.qA1.fresh');
ok(!!added && added.classList.contains('tree-added'), 'a key only B has renders as ADDED');
ok(!!added && added.textContent.indexOf('added') !== -1, 'the added row is labelled');

const removed = nodeAt(u, 'qubits.qA1.gone');
ok(!!removed, 'a key only A has RENDERS AT ALL (the whole point of union mode)');
ok(!!removed && removed.classList.contains('tree-removed'), 'it is marked removed');
ok(!!removed && removed.textContent.indexOf('7') !== -1,
   'it shows the value that was there: ' + JSON.stringify((removed || {}).textContent));

/* ══ 2. the delta is ValueDelta's, not a private formatter ══════════════ */
const chip = changed.querySelector('.val-delta');
ok(!!chip, 'the change carries a val-delta chip (the shared class)');
const grouped = window.ValueDelta.compute(1.0e-5, 3.0e-5);
ok(!!chip && chip.textContent.indexOf(grouped.text) !== -1,
   'the chip text is ValueDelta\'s: ' + JSON.stringify(chip ? chip.textContent : ''));
ok(!!chip && chip.textContent.indexOf('%') !== -1, 'and it carries the percentage');
ok(u.querySelectorAll('.delta-pos, .delta-neg, .delta-zero').length === 0,
   'the private delta-pos/neg/zero classes are gone');

/* ══ 3. a removed SUBTREE still expands ════════════════════════════════ */
const A2 = { qubits: { qA1: { xy: { operations: { x180: { amplitude: 0.1 } } } } } };
const B2 = { qubits: { qA1: {} } };   // the whole xy subtree removed in B
window.renderJsonTree('t-union', A2, { refData: B2, union: true, valueClick: 'diff',
                                       defaultDepth: 99 });
const sub = nodeAt(u, 'qubits.qA1.xy');
ok(!!sub && sub.classList.contains('tree-removed'), 'a removed subtree renders');
ok(!!nodeAt(u, 'qubits.qA1.xy.operations.x180.amplitude'),
   'and expands down to its leaves (depth 99 materialised them)');

/* ══ 4. diff mode is READ-ONLY ═════════════════════════════════════════ */
window.renderJsonTree('t-union', A, { refData: B, union: true, valueClick: 'diff',
                                      defaultDepth: 99 });
const val = nodeAt(u, 'qubits.qA1.T1').querySelector('.tree-val');
ok(val && val.style.cursor === 'copy',
   'a diff value offers COPY — never an editor against the loaded chip');
ok(u.querySelectorAll('.tree-json-edit-btn').length === 0,
   'no JSON-edit affordance in a comparison view');

/* ══ 5. REGRESSION: live diff is untouched ═════════════════════════════ */
const work = { qubits: { qA1: { T1: 1.0e-5, only_mine: 1 } } };
const liveDoc = { qubits: { qA1: { T1: 2.0e-5 } } };
window.renderJsonTree('t-live', work, { refData: liveDoc, valueClick: 'livediff',
                                        defaultDepth: 99 });
const lv = document.getElementById('t-live');
const lchanged = nodeAt(lv, 'qubits.qA1.T1');
ok(!!lchanged && lchanged.classList.contains('tree-diff'), 'live diff still marks a change');
ok(!!lchanged.querySelector('.tree-accept-btn') && !!lchanged.querySelector('.tree-reject-btn'),
   'live diff still offers accept / reject');
ok(!nodeAt(lv, 'qubits.qA1.only_mine').classList.contains('tree-added'),
   'live diff does NOT gain added/removed marks (union is opt-in)');
const ldelta = lchanged.querySelector('.val-delta');
ok(!!ldelta && ldelta.textContent.indexOf(
       window.ValueDelta.compute(1.0e-5, 2.0e-5).text) !== -1,
   'live diff reads after-minus-before through ValueDelta: ' +
   JSON.stringify(ldelta ? ldelta.textContent : ''));

/* ══ 6. REGRESSION: a plain tree is still editable ═════════════════════ */
window.renderJsonTree('t-plain', A, { defaultDepth: 99, crud: true });
const pl = document.getElementById('t-plain');
const pv = nodeAt(pl, 'qubits.qA1.T1').querySelector('.tree-val');
ok(pv && pv.style.cursor === 'pointer', 'the Explorer tree still opens the editor on click');
ok(pl.querySelectorAll('.tree-diff').length === 0, 'and carries no diff marks');

console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
process.exit(fails ? 1 : 0);
