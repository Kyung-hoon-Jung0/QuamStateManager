/* jsdom selfcheck for the Undo trail panel (undo-trail.js, 2026-08-28).
 * Pins, against the real app.js + undo-trail.js:
 *   1. a server undo (cellsReverted) opens the panel with the path, the value
 *      it went to, and a "go to field" button — no page navigation happens
 *   2. an in-memory step (quam:undo-step) shows from → to; a redo is labelled
 *   3. "go to field": a VISIBLE field is flashed + focused, nothing navigates;
 *      a field not on screen is handed to UndoNav.handle (the owning surface)
 *   4. × hides the panel; the next step brings it back; stateRestored clears
 *   5. the trail is bounded (8 steps)
 * Run: node tests/undo_trail_selfcheck.cjs   (driven by tests/test_undo_trail.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body><div id="pending-tray"></div>' +
    '<table id="bulk-table"><tr><td><input class="bulk-cell" id="c1" data-dot-path="qubits.q1.T1" value="1"></td></tr></table>' +
    '</body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
// jsdom has no layout: make the bulk cell count as visible, everything else not
const d = window.document;
const c1 = d.getElementById('c1');
c1.getClientRects = () => [{ width: 10, height: 10 }];
c1.scrollIntoView = () => { c1._scrolled = true; };
const handled = [];
window.UndoNav = { visibleEl: (dp) => (dp === 'qubits.q1.T1' ? c1 : null), handle: (entries) => { handled.push(entries[0].dot_path); } };

window.eval(fs.readFileSync(path.join(STATIC, 'undo-trail.js'), 'utf8'));
const panel = () => d.getElementById('undo-trail');

// 1. server undo
d.dispatchEvent(new window.CustomEvent('cellsReverted', { detail: { message: 'Undone: qubits.q1.T1 → 0.00001',
    entries: [{ dot_path: 'qubits.q1.T1', old_value_str: '1e-05', old_value_disp: '0.00001', old_kind: 'num' }] } }));
ok(!!panel() && !panel().hidden, 'a server undo opens the trail panel');
let t = panel().textContent;
ok(/qubits\.q1\.T1/.test(t) && /0\.00001/.test(t), 'the step names the path and the value it went to');
ok(panel().querySelector('.undo-trail-goto'), 'and carries a go-to-field button');
ok(handled.length === 0, 'nothing navigated by itself');
ok(/↶ undo/.test(t) && /staged/.test(t), 'labelled as an undo of a staged value');
// 2. in-memory step + redo label
d.dispatchEvent(new window.CustomEvent('quam:undo-step', { detail: { kind: 'redo', tier: 'memory', label: 'type 2 cells',
    entries: [{ dot_path: 'qubits.q2.T1', value: '3', from: '2' }] } }));
t = panel().textContent;
ok(/↷ redo/.test(t) && /typed/.test(t) && /type 2 cells/.test(t), 'an in-memory redo is labelled with its tier and label');
const latest = panel().querySelector('.undo-trail-latest');
ok(latest && /qubits\.q2\.T1/.test(latest.textContent) && /2\s*→\s*3/.test(latest.textContent.replace(/\s+/g, ' ')),
   'the newest step is first and shows from → to (' + latest.textContent.replace(/\s+/g, ' ').slice(0, 60) + ')');
// 3. go to field: visible → flash + focus, no navigation; hidden → UndoNav.handle
const btns = panel().querySelectorAll('.undo-trail-goto');
const visibleBtn = Array.prototype.find.call(btns, (b) => b.getAttribute('data-path') === 'qubits.q1.T1');
visibleBtn.click();
ok(c1.classList.contains('leu-flash') && c1._scrolled === true, 'go to field flashes + scrolls a VISIBLE field');
ok(d.activeElement === c1, 'and focuses it');
ok(handled.length === 0, 'without navigating anywhere');
const hiddenBtn = Array.prototype.find.call(btns, (b) => b.getAttribute('data-path') === 'qubits.q2.T1');
hiddenBtn.click();
ok(handled[0] === 'qubits.q2.T1', 'a field not on screen is handed to UndoNav (the owning surface) — on the press, not before');
// 4. hide / reappear / clear on restore
panel().querySelector('.undo-trail-close').click();
ok(panel().hidden === true, '× hides the panel');
d.dispatchEvent(new window.CustomEvent('quam:undo-step', { detail: { kind: 'undo', tier: 'memory', entries: [{ dot_path: 'qubits.q3.T1', value: '1', from: '5' }] } }));
ok(panel().hidden === false, 'the next step brings it back');
d.dispatchEvent(new window.CustomEvent('stateRestored'));
ok(window.UndoTrail.steps().length === 0 && /nothing undone yet/.test(panel().textContent), 'a state restore clears the trail');
// 5. bounded
for (let i = 0; i < 12; i++) {
    d.dispatchEvent(new window.CustomEvent('quam:undo-step', { detail: { kind: 'undo', tier: 'memory', entries: [{ dot_path: 'qubits.q' + i + '.T1', value: i, from: i + 1 }] } }));
}
ok(window.UndoTrail.steps().length === 8 && panel().querySelectorAll('.undo-trail-step').length === 8, 'the trail keeps the last 8 steps');
process.exit(fails ? 1 : 0);
