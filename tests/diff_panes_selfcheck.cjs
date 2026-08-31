/* jsdom selfcheck for docs/141 4z: the diff workbench's pane view client
 * (web/static/diff-panes.js).
 *  - clicking a pane title makes it the baseline: header + column marked,
 *    every other cell re-classed diff/same from the SERVER's PAIRWISE
 *    equality matrix (data-eq), never a JS equality
 *  - a Δ appears only on a cell that DIFFERS from the baseline and is numeric
 *    on both sides (window.ValueDelta), never on an equal or absent cell
 *  - the workbench's own htmx requests carry the CURRENT baseline
 *    (htmx:configRequest rewrite), the picker's hidden input follows, the URL
 *    is replaced with base=
 *  - re-arming is idempotent
 * Run: node tests/diff_panes_selfcheck.cjs   (driven by tests/test_diff_panes.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// three panes; rows: (1e-5, 2e-5, 1e-5) eq 101/010/101 ; ("a", absent, "a") eq 101/010/101 ; (5, 5, 7) eq 110/110/001
function cell(i, v, hasV) {
  return '<td class="dp-cell' + (i === 0 ? ' dp-base' : '') + '" data-i="' + i + '"' + (hasV ? ' data-v="' + v + '"' : '') + '>' +
    (v === null ? '<span class="muted dp-absent">–</span>' : '<span class="dp-val">' + v + '</span>') + '<span class="dp-delta" hidden></span></td>';
}
function dirRow(path, depth, count) {
  const parent = path.indexOf('.') >= 0 ? path.slice(0, path.lastIndexOf('.')) : '';
  return '<tr class="dp-row dp-dir" data-path="' + path + '" data-parent="' + parent + '" data-depth="' + depth + '"><td class="dp-key-col"><button type="button" class="dp-toggle" aria-expanded="true">▾</button><span class="dp-key dp-key-dir">' + path.split('.').pop() + '</span><span class="dp-count">' + count + '</span></td>' +
    '<td class="dp-cell dp-cell-dir" data-i="0"></td><td class="dp-cell dp-cell-dir" data-i="1"></td><td class="dp-cell dp-cell-dir" data-i="2"></td></tr>';
}
const TOOLS = '<div class="diff-panes-tools"><button type="button" class="btn-xs outline dp-depth" data-depth="0">0</button><button type="button" class="btn-xs outline dp-depth" data-depth="1">1</button><button type="button" class="btn-xs dp-depth" data-depth="99">All</button></div>';
const ROWS = dirRow('qubits', 0, 3) + dirRow('qubits.q1', 1, 3) +
  '<tr class="dp-row dp-leaf" data-eq="101,010,101" data-path="qubits.q1.T1" data-parent="qubits.q1" data-depth="2"><td class="dp-key-col"><code class="dot-path">qubits.q1.T1</code></td>' + cell(0, '1e-05', true) + cell(1, '2e-05', true) + cell(2, '1e-05', true) + '</tr>' +
  '<tr class="dp-row dp-leaf" data-eq="101,010,101" data-path="qubits.q1.name" data-parent="qubits.q1" data-depth="2"><td class="dp-key-col"><code class="dot-path">qubits.q1.name</code></td>' + cell(0, 'a', true) + cell(1, null, false) + cell(2, 'a', true) + '</tr>' +
  '<tr class="dp-row dp-leaf" data-eq="110,110,001" data-path="qubits.q1.n" data-parent="qubits.q1" data-depth="2"><td class="dp-key-col"><code class="dot-path">qubits.q1.n</code></td>' + cell(0, '5', true) + cell(1, '5', true) + cell(2, '7', true) + '</tr>';
const HEAD = ['A', 'B', 'C'].map((s, i) => '<th class="dp-pane-head' + (i === 0 ? ' dp-base' : '') + '" data-i="' + i + '"><button type="button" class="dp-pane-title" data-i="' + i + '"><span class="dp-slot">' + s + '</span><span class="dp-label">run ' + s + '</span><span class="dp-base-tag">baseline</span></button></th>').join('');
const DOM = '<div id="diff-root" data-base="0" data-n="3">' +
  '<form class="diff-wb-pickers"><input type="hidden" name="base" value="0"></form>' +
  '<div class="ph-tabs diff-wb-tabs"><button type="button" class="ph-tab" id="tab-node" hx-get="/diff?a=x&amp;b=y&amp;c=z&amp;d=&amp;e=&amp;tab=node&amp;view=panes&amp;base=0">node.json</button></div>' +
  '<div id="diff-panes" data-base="0" data-n="3">' + TOOLS + '<table id="diff-panes-table"><thead><tr><th class="dp-path-col">Leaf</th>' + HEAD + '</tr></thead><tbody>' + ROWS + '</tbody></table></div></div>' +
  '<button type="button" id="outside" hx-get="/diff?a=q&amp;b=r&amp;base=0">outside</button>';

const dom = new JSDOM('<!doctype html><html><body>' + DOM + '</body></html>', { url: 'http://localhost/diff?a=x&b=y&c=z&tab=state&base=0', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));      // window.ValueDelta
window.eval(fs.readFileSync(path.join(STATIC, 'diff-panes.js'), 'utf8'));

(async function main() {
// jsdom: readyState is 'loading' at eval time, the arm waits for DOMContentLoaded
await new Promise((r) => setTimeout(r, 20));

const d = window.document;
const root = d.getElementById('diff-panes');
const rows = () => Array.from(root.querySelectorAll('tr.dp-row.dp-leaf'));
const allRows = () => Array.from(root.querySelectorAll('tr.dp-row'));
const cls = (r, i) => { const td = rows()[r].querySelectorAll('td.dp-cell')[i]; return ['dp-base', 'dp-diff', 'dp-same'].filter((c) => td.classList.contains(c)).join('|') || '-'; };
const delta = (r, i) => { const el = rows()[r].querySelectorAll('td.dp-cell')[i].querySelector('.dp-delta'); return el.hidden ? null : el.textContent.trim(); };
const heads = () => Array.from(root.querySelectorAll('.dp-pane-head')).map((h) => h.classList.contains('dp-base') ? 1 : 0).join('');

ok(!!window.DiffPanes && root._dpArmed === true, 'the pane root is armed at load');
ok(heads() === '100', 'A is the baseline as rendered');

// click C
d.querySelectorAll('.dp-pane-title')[2].click();
ok(heads() === '001' && root.getAttribute('data-base') === '2', 'clicking pane C makes it the baseline');
ok(cls(0, 2) === 'dp-base' && cls(0, 0) === 'dp-same' && cls(0, 1) === 'dp-diff', 'row 1 (1e-5, 2e-5, 1e-5): A same as C, B differs');
ok(delta(0, 1) !== null && /\+0\.00001|1e-05|\+100%/.test(delta(0, 1)), 'B carries a Δ against C: ' + delta(0, 1));
ok(delta(0, 0) === null && delta(0, 2) === null, 'no Δ on the equal cell or the baseline');
ok(cls(1, 1) === 'dp-diff' && delta(1, 1) === null, 'an ABSENT cell differs from the baseline and carries no Δ');
ok(cls(1, 0) === 'dp-same', 'a string equal to the baseline is "same" (server group), no JS equality');
const minus2 = (s) => s !== null && (s.indexOf('-2') === 0 || s.indexOf('−2') === 0);
ok(cls(2, 0) === 'dp-diff' && cls(2, 1) === 'dp-diff' && minus2(delta(2, 0)) && minus2(delta(2, 1)), 'row 3 (5, 5, 7) against C=7: both A and B differ, Δ -2 each: ' + delta(2, 0));
ok(d.querySelector('.diff-wb-pickers input[name="base"]').value === '2', 'the picker hidden input follows');
ok(d.getElementById('diff-root').getAttribute('data-base') === '2', '#diff-root carries the baseline');
ok(/[?&]base=2(&|$)/.test(window.location.search), 'the URL is replaced with base=2: ' + window.location.search);

// htmx:configRequest rewrite -- only for requests from INSIDE the workbench
function configRequest(elt, p) {
  const ev = new window.CustomEvent('htmx:configRequest', { bubbles: true, detail: { path: p, elt: elt, parameters: {}, verb: 'get' } });
  elt.dispatchEvent(ev);
  return ev.detail.path;
}
const tab = d.getElementById('tab-node');
ok(configRequest(tab, '/diff?a=x&b=y&c=z&d=&e=&tab=node&view=panes&base=0') === '/diff?a=x&b=y&c=z&d=&e=&tab=node&view=panes&base=2', 'a tab-strip request is rewritten to the current baseline');
ok(configRequest(d.getElementById('outside'), '/diff?a=q&b=r&base=0') === '/diff?a=q&b=r&base=0', 'a request from outside the workbench is left alone');
ok(configRequest(tab, '/bulk?base=0') === '/bulk?base=0', 'a non-/diff path is left alone');

// click B, then A again: full round trip, no leftovers
d.querySelectorAll('.dp-pane-title')[1].click();
ok(heads() === '010' && cls(0, 1) === 'dp-base' && cls(0, 0) === 'dp-diff' && cls(0, 2) === 'dp-diff' && cls(1, 0) === 'dp-diff' && cls(1, 2) === 'dp-diff', 'B as baseline: row 1 A and C differ; row 2 (B absent) A and C differ');
ok(delta(1, 0) === null, 'no Δ against an absent baseline');
d.querySelectorAll('.dp-pane-title')[0].click();
ok(heads() === '100' && cls(0, 0) === 'dp-base' && cls(0, 1) === 'dp-diff' && cls(0, 2) === 'dp-same' && cls(2, 2) === 'dp-diff' && delta(2, 2) !== null && delta(2, 2).indexOf('+2') === 0, 'back to A: the original painting, Δ +2 on C in row 3: ' + delta(2, 2));
ok(Array.from(root.querySelectorAll('tr.dp-leaf td.dp-cell')).every((td) => ['dp-base', 'dp-diff', 'dp-same'].filter((c) => td.classList.contains(c)).length === 1), 'every cell carries exactly one of base/diff/same');

ok(Array.from(root.querySelectorAll('tr.dp-dir td.dp-cell')).every((td) => !td.classList.contains('dp-diff') && !td.classList.contains('dp-same') && !td.classList.contains('dp-base')), 'a container row is never painted (no values)');

// 4ab: the key tree -- collapse hides descendants only, depth buttons, glyphs
{
  const dirs = allRows().filter((r) => r.classList.contains('dp-dir'));
  ok(dirs.length === 2 && rows().length === 3 && allRows().every((r) => !r.hidden), 'fixture: 2 containers, 3 leaves, all visible');
  dirs[1].querySelector('.dp-toggle').click();          // collapse qubits.q1
  ok(dirs[1].hasAttribute('data-collapsed') && dirs[1].querySelector('.dp-toggle').textContent === '▸' && dirs[1].querySelector('.dp-toggle').getAttribute('aria-expanded') === 'false', 'collapsing a container flips its toggle');
  ok(rows().every((r) => r.hidden) && !dirs[0].hidden && !dirs[1].hidden, 'its three leaves hide; the container itself and its parent stay');
  dirs[1].querySelector('.dp-toggle').click();
  ok(allRows().every((r) => !r.hidden), 'expanding shows them again');
  dirs[0].querySelector('.dp-toggle').click();          // collapse the root container
  ok(dirs[1].hidden && rows().every((r) => r.hidden), 'collapsing the ROOT hides the nested container and every leaf (ancestor walk)');
  dirs[1].setAttribute('data-collapsed', '1');           // nested collapsed too, then open the root
  dirs[0].querySelector('.dp-toggle').click();
  ok(!dirs[1].hidden && rows().every((r) => r.hidden), 'opening the root shows the nested container but NOT its leaves (it is still collapsed)');
  root.querySelector('.dp-depth[data-depth="1"]').click();
  ok(!dirs[0].hasAttribute('data-collapsed') && dirs[1].hasAttribute('data-collapsed') && rows().every((r) => r.hidden), 'Depth 1: containers at depth >= 1 collapse');
  root.querySelector('.dp-depth[data-depth="99"]').click();
  ok(allRows().every((r) => !r.hidden) && !root.querySelector('.dp-depth[data-depth="99"]').classList.contains('outline'), 'All: everything visible, the active depth button is filled');
  root.querySelector('.dp-depth[data-depth="0"]').click();
  ok(dirs[0].hasAttribute('data-collapsed') && allRows().filter((r) => !r.hidden).length === 1, 'Depth 0: only the root container row remains');
  root.querySelector('.dp-depth[data-depth="99"]').click();
  // a baseline switch while collapsed keeps the visibility (paint and visibility are independent)
  root.querySelector('.dp-depth[data-depth="1"]').click();
  d.querySelectorAll('.dp-pane-title')[1].click();
  ok(rows().every((r) => r.hidden) && heads() === '010', 'a baseline switch does not un-collapse anything');
  root.querySelector('.dp-depth[data-depth="99"]').click();
  d.querySelectorAll('.dp-pane-title')[0].click();
}

// docs/141 4ac -- the non-transitive tolerance. b and c ARE equal under the
// app's one rule while a differs from c: with B as the baseline, C must read
// dp-same and carry no delta. Group ids could not express this (b and c landed
// in different classes), so this row is the regression that replaced them.
{
  const tb = root.querySelector('tbody');
  tb.insertAdjacentHTML('beforeend',
    '<tr class="dp-row dp-leaf" id="tol-row" data-eq="110,111,011" data-path="qubits.q1.f_01" data-parent="qubits.q1" data-depth="2">' +
    '<td class="dp-key-col"><code class="dot-path">qubits.q1.f_01</code></td>' +
    cell(0, '1', true) + cell(1, '1.0000000009', true) + cell(2, '1.0000000018', true) + '</tr>');
  window.DiffPanes.paint(root, 1);
  const tds = d.getElementById('tol-row').querySelectorAll('td.dp-cell');
  const kind = (i) => ['dp-base', 'dp-diff', 'dp-same'].filter((c) => tds[i].classList.contains(c)).join('|');
  const dl = (i) => { const e = tds[i].querySelector('.dp-delta'); return e.hidden ? null : e.textContent.trim(); };
  ok(kind(1) === 'dp-base' && kind(0) === 'dp-same' && kind(2) === 'dp-same',
     'a non-transitive tolerance: against B, both A and C read as SAME');
  ok(dl(0) === null && dl(2) === null, 'and neither equal cell carries a fabricated Δ');
  window.DiffPanes.paint(root, 0);
  ok(kind(2) === 'dp-diff', 'against A, C differs -- the row is listed for a reason');
  d.getElementById('tol-row').remove();
  window.DiffPanes.paint(root, 0);
}

// docs/141 4ac -- a key that is a leaf on one side and a container on another.
// The server gives the value row its own key; the client must collapse the
// container over BOTH the value row and the real children, and must not walk
// a self-loop.
{
  const tb = root.querySelector('tbody');
  const before = tb.innerHTML;
  tb.insertAdjacentHTML('beforeend',
    dirRow('extras', 0, 2).replace('data-path="extras"', 'data-path="extras"') +
    '<tr class="dp-row dp-dir" data-path="extras.note" data-parent="extras" data-depth="1"><td class="dp-key-col"><button type="button" class="dp-toggle" aria-expanded="true">\u25be</button><span class="dp-key dp-key-dir">note</span><span class="dp-count">2</span></td><td class="dp-cell dp-cell-dir" data-i="0"></td><td class="dp-cell dp-cell-dir" data-i="1"></td><td class="dp-cell dp-cell-dir" data-i="2"></td></tr>' +
    '<tr class="dp-row dp-leaf" id="val-row" data-eq="100,010,001" data-path="extras.note\u0000value" data-parent="extras.note" data-depth="2"><td class="dp-key-col"><code class="dot-path">extras.note</code></td>' + cell(0, 'x', true) + cell(1, 'y', true) + cell(2, 'z', true) + '</tr>' +
    '<tr class="dp-row dp-leaf" id="kid-row" data-eq="100,010,001" data-path="extras.note.deep" data-parent="extras.note" data-depth="2"><td class="dp-key-col"><code class="dot-path">extras.note.deep</code></td>' + cell(0, '1', true) + cell(1, '2', true) + cell(2, '3', true) + '</tr>');
  const dirNote = root.querySelector('tr.dp-dir[data-path="extras.note"]');
  const dirEx = root.querySelector('tr.dp-dir[data-path="extras"]');
  const val = d.getElementById('val-row'), kid = d.getElementById('kid-row');
  dirNote.querySelector('.dp-toggle').click();
  ok(val.hidden && kid.hidden && !dirNote.hidden,
     'collapsing the doubled container hides its value row AND its real child');
  dirNote.querySelector('.dp-toggle').click();
  ok(!val.hidden && !kid.hidden, 'expanding shows both again');
  dirEx.querySelector('.dp-toggle').click();
  ok(dirNote.hidden && val.hidden && kid.hidden,
     'collapsing the ancestor leaves no orphan under the doubled key');
  dirEx.querySelector('.dp-toggle').click();
  root.querySelector('.dp-depth[data-depth="0"]').click();
  ok(![dirNote, val, kid].some((r) => !r.hidden), 'Depth 0 collapses every container, doubled key included');
  root.querySelector('.dp-depth[data-depth="99"]').click();
  tb.innerHTML = before;
  window.DiffPanes.arm(root);
}

// docs/141 4ac -- the collapse map's two defences, exercised against markup
// that CARRIES the old defect: a dir row and a leaf row under the same
// data-path, and a row whose parent is itself.
{
  const tb = root.querySelector('tbody');
  const before = tb.innerHTML;
  tb.insertAdjacentHTML('beforeend',
    '<tr class="dp-row dp-dir" id="dup-dir" data-path="dup" data-parent="" data-depth="0"><td class="dp-key-col"><button type="button" class="dp-toggle" aria-expanded="true">\u25be</button><span class="dp-key dp-key-dir">dup</span><span class="dp-count">1</span></td><td class="dp-cell dp-cell-dir" data-i="0"></td><td class="dp-cell dp-cell-dir" data-i="1"></td><td class="dp-cell dp-cell-dir" data-i="2"></td></tr>' +
    // the SAME data-path as the container above (the pre-4ac server markup)
    '<tr class="dp-row dp-leaf" id="dup-leaf" data-eq="100,010,001" data-path="dup" data-parent="dup" data-depth="1"><td class="dp-key-col"><code class="dot-path">dup</code></td>' + cell(0, 'x', true) + cell(1, 'y', true) + cell(2, 'z', true) + '</tr>' +
    '<tr class="dp-row dp-leaf" id="dup-kid" data-eq="100,010,001" data-path="dup.kid" data-parent="dup" data-depth="1"><td class="dp-key-col"><code class="dot-path">dup.kid</code></td>' + cell(0, '1', true) + cell(1, '2', true) + cell(2, '3', true) + '</tr>');
  const dupDir = d.getElementById('dup-dir');
  const dupLeaf = d.getElementById('dup-leaf');
  const dupKid = d.getElementById('dup-kid');
  dupDir.setAttribute('data-collapsed', '1');
  window.DiffPanes.applyVisibility(root);
  ok(dupKid.hidden, 'a duplicate data-path never lets a leaf shadow the container that owns the toggle');
  ok(dupLeaf.hidden, 'and the shadowing leaf hides with it');
  dupDir.removeAttribute('data-collapsed');
  window.DiffPanes.applyVisibility(root);
  ok(!dupKid.hidden && !dupLeaf.hidden, 'expanding shows both again');

  // a row whose parent is ITSELF: the walk must stop, not spin to its guard
  tb.insertAdjacentHTML('beforeend',
    '<tr class="dp-row dp-leaf" id="self-row" data-eq="100,010,001" data-path="loop" data-parent="loop" data-depth="0"><td class="dp-key-col"><code class="dot-path">loop</code></td>' + cell(0, '1', true) + cell(1, '2', true) + cell(2, '3', true) + '</tr>');
  const selfRow = d.getElementById('self-row');
  // a row whose parent resolves to ITSELF must not be treated as its own
  // ancestor: with the guard the walk stops, without it the row collapses
  // itself the moment anything marks it collapsed.
  selfRow.setAttribute('data-collapsed', '1');
  const t0 = Date.now();
  window.DiffPanes.applyVisibility(root);
  ok(!selfRow.hidden, 'a self-referencing parent is not its own ancestor (the walk stops at it)');
  ok((Date.now() - t0) < 500, 'and the walk terminates promptly');
  selfRow.removeAttribute('data-collapsed');
  window.DiffPanes.applyVisibility(root);
  tb.innerHTML = before;
  window.DiffPanes.arm(root);
}

// idempotent re-arm (an htmx swap fires afterSwap)
d.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true }));
d.querySelectorAll('.dp-pane-title')[2].click();
ok(heads() === '001', 'after a swap event one click still means one switch (no double handlers)');

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (44 assertions)');
process.exit(fails ? 1 : 0);
})();
