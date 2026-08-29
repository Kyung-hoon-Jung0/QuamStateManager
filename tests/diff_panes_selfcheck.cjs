/* jsdom selfcheck for docs/141 4z: the diff workbench's pane view client
 * (web/static/diff-panes.js).
 *  - clicking a pane title makes it the baseline: header + column marked,
 *    every other cell re-classed diff/same from the SERVER's equality groups
 *    (data-groups), never a JS equality
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

// three panes; rows: (1e-5, 2e-5, 1e-5) groups 0,1,0 ; ("a", absent, "a") groups 0,-1,0 ; (5, 5, 7) groups 0,0,1
function cell(i, v, hasV) {
  return '<td class="dp-cell' + (i === 0 ? ' dp-base' : '') + '" data-i="' + i + '"' + (hasV ? ' data-v="' + v + '"' : '') + '>' +
    (v === null ? '<span class="muted dp-absent">–</span>' : '<span class="dp-val">' + v + '</span>') + '<span class="dp-delta" hidden></span></td>';
}
const ROWS =
  '<tr class="dp-row" data-groups="0,1,0"><td class="dp-path-col"><code class="dot-path">qubits.q1.T1</code></td>' + cell(0, '1e-05', true) + cell(1, '2e-05', true) + cell(2, '1e-05', true) + '</tr>' +
  '<tr class="dp-row" data-groups="0,-1,0"><td class="dp-path-col"><code class="dot-path">qubits.q1.name</code></td>' + cell(0, 'a', true) + cell(1, null, false) + cell(2, 'a', true) + '</tr>' +
  '<tr class="dp-row" data-groups="0,0,1"><td class="dp-path-col"><code class="dot-path">qubits.q1.n</code></td>' + cell(0, '5', true) + cell(1, '5', true) + cell(2, '7', true) + '</tr>';
const HEAD = ['A', 'B', 'C'].map((s, i) => '<th class="dp-pane-head' + (i === 0 ? ' dp-base' : '') + '" data-i="' + i + '"><button type="button" class="dp-pane-title" data-i="' + i + '"><span class="dp-slot">' + s + '</span><span class="dp-label">run ' + s + '</span><span class="dp-base-tag">baseline</span></button></th>').join('');
const DOM = '<div id="diff-root" data-base="0" data-n="3">' +
  '<form class="diff-wb-pickers"><input type="hidden" name="base" value="0"></form>' +
  '<div class="ph-tabs diff-wb-tabs"><button type="button" class="ph-tab" id="tab-node" hx-get="/diff?a=x&amp;b=y&amp;c=z&amp;d=&amp;e=&amp;tab=node&amp;view=panes&amp;base=0">node.json</button></div>' +
  '<div id="diff-panes" data-base="0" data-n="3"><table id="diff-panes-table"><thead><tr><th class="dp-path-col">Leaf</th>' + HEAD + '</tr></thead><tbody>' + ROWS + '</tbody></table></div></div>' +
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
const rows = () => Array.from(root.querySelectorAll('tr.dp-row'));
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
ok(Array.from(root.querySelectorAll('td.dp-cell')).every((td) => ['dp-base', 'dp-diff', 'dp-same'].filter((c) => td.classList.contains(c)).length === 1), 'every cell carries exactly one of base/diff/same');

// idempotent re-arm (an htmx swap fires afterSwap)
d.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true }));
d.querySelectorAll('.dp-pane-title')[2].click();
ok(heads() === '001', 'after a swap event one click still means one switch (no double handlers)');

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (21 assertions)');
process.exit(fails ? 1 : 0);
})();
