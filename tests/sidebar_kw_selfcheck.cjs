/* Customer feedback 2026-09-08 -- keyword chips under the sidebar experiment
 * filter, in the REAL app.js under jsdom: one click ADDS the token to the
 * box (AND with what is typed), a second click REMOVES it, typing keeps the
 * chips lit in sync (case-insensitive), the tree refetch is triggered
 * through the box's own hx-trigger. docs/236 (customer 2026-10-01): every row
 * is ONE line with a trailing "…"; hovering "…" opens the row, the mouse
 * leaving it folds it (after a short delay); a click toggles (keyboard,
 * touch); a selected chip folded away lights the "…".
 *
 * Run: node tests/sidebar_kw_selfcheck.cjs  (driven by tests/test_sidebar_kw.py)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
const store = {};
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
const triggers = [];
window.htmx = { ajax: () => Promise.resolve(), trigger: (el, ev) => { triggers.push([el && el.id, ev]); }, process: () => {} };
global.htmx = window.htmx;

const doc = window.document;
function chip(kw, extra) { return '<button type="button" class="sb-kw-chip' + (extra || '') + '" data-kw="' + kw + '">' + kw + '</button>'; }
doc.body.innerHTML =
    '<textarea id="sidebar-filter-input"></textarea><div class="filter-tags" id="filter-tags"></div>'
    + '<div class="sb-kw" id="sidebar-kw" data-for="sidebar-filter-input">'
    + '<div class="sb-kw-slot"><div class="sb-kw-group sb-kw-qubits sb-kw-fold">' + chip('q1') + chip('q2') + chip('q10')
    + '<button type="button" class="sb-kw-chip sb-kw-more" aria-expanded="false">…</button></div></div>'
    + '<div class="sb-kw-slot"><div class="sb-kw-group sb-kw-pairs sb-kw-fold">' + chip('q1-q2')
    + '<button type="button" class="sb-kw-chip sb-kw-more" aria-expanded="false">…</button></div></div>'
    + '<div class="sb-kw-slot"><div class="sb-kw-group sb-kw-words sb-kw-fold">' + chip('res') + chip('rabi') + chip('ramsey')
    + chip('spec') + chip('T1') + chip('status:error')
    + '<button type="button" class="sb-kw-chip sb-kw-more" id="sidebar-kw-more" aria-expanded="false">…</button></div></div>'
    + '</div>';

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const input = doc.getElementById('sidebar-filter-input');
const C = (kw) => doc.querySelector('.sb-kw-chip[data-kw="' + kw + '"]');
const lit = () => Array.from(doc.querySelectorAll('.sb-kw-chip.active[data-kw]')).map((c) => c.getAttribute('data-kw')).sort().join(' ');
function click(el) { el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })); }

// 1. one click adds the token, lights the chip, refetches the tree through the box's own trigger
triggers.length = 0;
click(C('rabi'));
ok(input.value === 'rabi', 'a click puts the keyword in the box: ' + JSON.stringify(input.value));
ok(lit() === 'rabi' && C('rabi').getAttribute('aria-pressed') === 'true', 'the chip is lit (active + aria-pressed)');
ok(triggers.some((t) => t[0] === 'sidebar-filter-input' && t[1] === 'input'), 'the tree refetch rides the box\'s own hx-trigger (input)');
ok(doc.querySelectorAll('#filter-tags .filter-tag').length === 1, 'the filter pill row follows');
// 2. a second keyword ANDs with the first; a qubit id is a bare token (exact match server-side)
click(C('q1'));
ok(input.value === 'rabi q1' && lit() === 'q1 rabi', 'a second click ANDs the token (rabi q1)');
click(C('q1-q2'));
ok(input.value === 'rabi q1 q1-q2', 'a pair chip adds the pair token');
// 3. clicking a lit chip removes ONLY its token
click(C('rabi'));
ok(input.value === 'q1 q1-q2' && lit() === 'q1 q1-q2', 'clicking a lit chip removes its token and unlights it, the others stay');
ok(!C('q10').classList.contains('active'), 'q10 is not lit by q1 (whole-token match, not substring)');
// 4. typing keeps the chips in sync, case-insensitively
input.value = 'q1 q1-q2 RAMSEY';
input.dispatchEvent(new window.Event('input', { bubbles: true }));
ok(C('ramsey').classList.contains('active'), 'a typed RAMSEY lights the ramsey chip');
click(C('ramsey'));
ok(input.value === 'q1 q1-q2' && !C('ramsey').classList.contains('active'), 'clicking it removes the typed token whatever its case');
input.value = 'q1 q1-q2 t1';
input.dispatchEvent(new window.Event('input', { bubbles: true }));
ok(C('T1').classList.contains('active'), 'a typed t1 lights the T1 chip (the chip side is case-folded too)');
click(C('T1'));
ok(input.value === 'q1 q1-q2', 'clicking T1 removes the typed t1');
// 5. docs/236: the fold. jsdom has no layout, so the fold's hidden set is set by
// hand where layout would put it (fit() needs a real width).
const words = doc.querySelector('.sb-kw-words'), more = doc.getElementById('sidebar-kw-more');
const F = window.SidebarKwFold;
ok(!!F && typeof F.open === 'function', 'the fold module is there');
const over = ['spec', 'T1', 'status:error'];
over.forEach((k) => C(k).classList.add('sb-kw-over'));
function hover(el) { el.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true })); }
function leave(el, to) { el.dispatchEvent(new window.MouseEvent('mouseout', { bubbles: true, relatedTarget: to || null })); }
hover(more);
ok(words.classList.contains('sb-kw-open') && more.getAttribute('aria-expanded') === 'true'
   && over.every((k) => !C(k).classList.contains('sb-kw-over')), 'hovering "…" opens the row: every folded chip shows');
click(C('status:error'));
ok(input.value === 'q1 q1-q2 status:error' && C('status:error').classList.contains('active'),
   'a chip picked from the open row adds its token (failed = status:error)');
leave(C('status:error'), C('T1'));
ok(words.classList.contains('sb-kw-open'), 'moving between its own chips keeps it open');
leave(C('T1'), doc.body);
ok(words.classList.contains('sb-kw-open'), 'leaving does not fold at once (a short grace, so a slip back in keeps it)');
setTimeout(() => {
    ok(!words.classList.contains('sb-kw-open') && more.getAttribute('aria-expanded') === 'false',
       'the mouse leaving the open row folds it');
    // the fold re-hides what does not fit (layout-less here: re-mark by hand) and
    // a SELECTED chip folded away lights "…"
    over.forEach((k) => C(k).classList.add('sb-kw-over'));
    window.sidebarKwSync();
    ok(more.classList.contains('sb-kw-more-on') && /status:error selected/.test(more.title),
       'a selected chip folded away lights "…" and names it');
    // a click toggles (keyboard / touch)
    click(more);
    ok(words.classList.contains('sb-kw-open'), 'a click on "…" opens the row');
    click(more);
    ok(!words.classList.contains('sb-kw-open'), 'a second click folds it');
    // only one row is open at a time
    const qmore = doc.querySelector('.sb-kw-qubits .sb-kw-more');
    hover(qmore); hover(more);
    ok(words.classList.contains('sb-kw-open') && !doc.querySelector('.sb-kw-qubits').classList.contains('sb-kw-open'),
       'opening one row folds the other');
    F.close(words);
    ok(lit() === 'q1 q1-q2 status:error', 'the fold never touches the lit set');
// 7. clearing the box unlights everything
input.value = '';
input.dispatchEvent(new window.Event('input', { bubbles: true }));
ok(lit() === '', 'an empty box lights no chip');
ok(!more.classList.contains('sb-kw-more-on'), 'and "…" goes quiet with it');
process.exit(fails ? 1 : 0);
}, (window.SidebarKwFold && window.SidebarKwFold.CLOSE_MS || 280) + 120);
