/* jsdom selfcheck for the inline-edit commit plumbing (docs/75), running the
 * REAL app.js. These pin the Pulses hotfix — every check below corresponds to
 * a behaviour that was broken in the browser before it:
 *
 *  C1. A focusout fired BY the commit's own re-render (form still in flight)
 *      must NOT re-submit — that double commit is what handed the browser a
 *      native form submission ("Leave site?" / silent full-page reload).
 *  C2. An ordinary click-away with a changed value still commits (the feature
 *      the focusout handler exists for).
 *  C3. An unchanged value never commits.
 *  C4. An htmx-owned form never keeps the native submission's default action.
 *  C5. Enter remembers the field; after the pane re-renders, focus and caret
 *      come back to it (otherwise focus lands on <body> and the next Enter
 *      does nothing).
 *  C6. Tab remembers where focus was HEADING (index within the pane) and
 *      restores there, so Tab keeps walking the form across the re-render.
 *  C7. When focus left the pane entirely, nothing is yanked back.
 *  C8. Focus the user has since moved is never stolen.
 *  C9. The panel scroll position is restored across the re-render.
 *
 * Run: node tests/pulses_commit_selfcheck.cjs  (driven by tests/test_pulses_commit.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane"></div>' +
    '<div id="inspector-pane"></div>' +
    '<div id="pending-tray" data-change-count="1"></div>' +
    '<button id="outside-btn">outside</button>' +
    '</body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.FocusEvent = window.FocusEvent;
global.navigator = window.navigator;
global.location = window.location;
function mkStorage() {
    const m = new Map();
    return { getItem: (k) => (m.has(k) ? m.get(k) : null),
             setItem: (k, v) => m.set(k, String(v)),
             removeItem: (k) => m.delete(k) };
}
global.localStorage = mkStorage();
global.sessionStorage = mkStorage();
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.Element.prototype.getClientRects = function () { return [{}]; };
window.htmx = { ajax: function () { return Promise.resolve(); },
                trigger: function () {}, process: function () {} };
global.htmx = window.htmx;
window.fetch = global.fetch = function () {
    return Promise.resolve({ status: 200, json: () => Promise.resolve({}),
                             text: () => Promise.resolve('') });
};
window.confirm = function () { return true; };

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

/* ------------------------------------------------------------------ */
/* Fixture: a pulse-detail-shaped inspector pane                        */
/* ------------------------------------------------------------------ */

const PARAMS = ['length', 'amplitude', 'sigma'];

function renderPane(values) {
    const pane = window.document.getElementById('inspector-pane');
    pane.innerHTML = PARAMS.map(function (p) {
        const v = values[p];
        return '<form class="inline-edit pulse-edit-form" hx-post="/pulse/edit">' +
               '<input type="hidden" name="dot_path" value="qubits.q1.xy.operations.x180.' + p + '">' +
               '<input type="text" name="value" class="edit-input" data-param="' + p + '"' +
               ' data-committed="' + v + '" value="' + v + '">' +
               '<button type="button" class="pulse-tune-btn">~</button>' +
               '</form>';
    }).join('');
    return pane;
}

function inputFor(param) {
    return window.document.querySelector(
        '#inspector-pane input[data-param="' + param + '"]');
}

/* requestSubmit is not implemented by jsdom — count calls instead. */
const submits = [];
window.HTMLFormElement.prototype.requestSubmit = function () {
    submits.push(this.querySelector('input[data-param]').getAttribute('data-param'));
};

function fireFocusOut(input, relatedTarget) {
    const ev = new window.FocusEvent('focusout',
        { bubbles: true, relatedTarget: relatedTarget || null });
    input.dispatchEvent(ev);
}
function fireEnter(input) {
    input.dispatchEvent(new window.KeyboardEvent('keydown',
        { key: 'Enter', bubbles: true }));
}
function settle() {
    // what htmx fires after swapping #inspector-pane
    const pane = window.document.getElementById('inspector-pane');
    pane.dispatchEvent(new window.CustomEvent('htmx:afterSettle',
        { bubbles: true, detail: {} }));
}

(async function () {

// ---- C1: the commit's own swap-induced focusout must not re-submit --------
renderPane({ length: 40, amplitude: 0.1, sigma: 5 });
let inp = inputFor('length');
inp.value = '44';
submits.length = 0;
inp.closest('form').classList.add('htmx-request');   // htmx: request in flight
fireFocusOut(inp, null);
ok(submits.length === 0,
   'C1a in-flight form (htmx-request) is never re-submitted by focusout');

inp.closest('form').classList.remove('htmx-request');
inp.closest('form').dataset.committing = '1';        // our own in-flight flag
submits.length = 0;
fireFocusOut(inp, null);
ok(submits.length === 0, 'C1b the data-committing flag also blocks the re-submit');
delete inp.closest('form').dataset.committing;

// the flag is set/cleared by the real htmx request lifecycle listeners
const form = inp.closest('form');
window.document.dispatchEvent(new window.CustomEvent('htmx:beforeRequest',
    { bubbles: true, detail: { elt: form } }));
ok(form.dataset.committing === '1', 'C1c htmx:beforeRequest marks the form committing');
window.document.dispatchEvent(new window.CustomEvent('htmx:afterRequest',
    { bubbles: true, detail: { elt: form } }));
ok(!form.dataset.committing, 'C1d htmx:afterRequest clears it');

// ---- C2 / C3: the click-away commit itself still works -------------------
submits.length = 0;
inp = inputFor('length');
inp.value = '44';
fireFocusOut(inp, window.document.getElementById('outside-btn'));
ok(submits.length === 1 && submits[0] === 'length',
   'C2 a changed value still commits on click-away');

submits.length = 0;
inp = inputFor('amplitude');
inp.value = inp.getAttribute('data-committed');
fireFocusOut(inp, window.document.getElementById('outside-btn'));
ok(submits.length === 0, 'C3 an unchanged value never commits');

// ---- C4: htmx-owned forms never navigate natively ------------------------
let prevented = null;
const f = inputFor('length').closest('form');
let ev = new window.Event('submit', { bubbles: true, cancelable: true });
f.dispatchEvent(ev);
prevented = ev.defaultPrevented;
ok(prevented === true, 'C4a a form with hx-post has its native submission prevented');

const plain = window.document.createElement('form');
window.document.body.appendChild(plain);
ev = new window.Event('submit', { bubbles: true, cancelable: true });
plain.dispatchEvent(ev);
ok(ev.defaultPrevented === false, 'C4b a plain (non-htmx) form is left alone');

const savedHtmx = window.htmx;
window.htmx = undefined;
ev = new window.Event('submit', { bubbles: true, cancelable: true });
f.dispatchEvent(ev);
ok(ev.defaultPrevented === false,
   'C4c without htmx loaded the native submission is the fallback (not blocked)');
window.htmx = savedHtmx;

// ---- C5: Enter → focus + caret come back after the re-render -------------
renderPane({ length: 40, amplitude: 0.1, sigma: 5 });
inp = inputFor('length');
inp.focus();
inp.value = '44';
try { inp.setSelectionRange(2, 2); } catch (e) {}
fireEnter(inp);
renderPane({ length: 44, amplitude: 0.1, sigma: 5 });   // the commit's re-render
window.document.body.focus();                            // swap dropped focus
settle();
await flush(10);
let active = window.document.activeElement;
ok(active && active.getAttribute && active.getAttribute('data-param') === 'length',
   'C5a Enter restores focus to the committed field');
ok(active === inputFor('length') && active !== inp,
   'C5b focus lands on the RE-RENDERED element, not the discarded one');
ok(active.selectionStart === 2, 'C5c the caret position is preserved');

// ---- C6: Tab restores the destination inside the pane --------------------
renderPane({ length: 44, amplitude: 0.1, sigma: 5 });
inp = inputFor('length');
inp.focus();
inp.value = '48';
const tuneBtn = inp.closest('form').querySelector('.pulse-tune-btn');
submits.length = 0;
fireFocusOut(inp, tuneBtn);            // Tab's natural next stop
ok(submits.length === 1, 'C6a Tab commits');
renderPane({ length: 48, amplitude: 0.1, sigma: 5 });
window.document.body.focus();
settle();
await flush(10);
active = window.document.activeElement;
ok(active && active.classList && active.classList.contains('pulse-tune-btn'),
   'C6b Tab lands on the same next tab stop after the re-render');

// ---- C7: focus that left the pane is not yanked back ---------------------
renderPane({ length: 48, amplitude: 0.1, sigma: 5 });
inp = inputFor('length');
inp.focus();
inp.value = '52';
fireFocusOut(inp, window.document.getElementById('outside-btn'));
renderPane({ length: 52, amplitude: 0.1, sigma: 5 });
window.document.body.focus();
settle();
await flush(10);
ok(window.document.activeElement === window.document.body,
   'C7 a commit that moved focus out of the pane never pulls it back');

// ---- C8: focus the user has since moved is never stolen ------------------
renderPane({ length: 52, amplitude: 0.1, sigma: 5 });
inp = inputFor('length');
inp.focus();
inp.value = '56';
fireEnter(inp);
renderPane({ length: 56, amplitude: 0.1, sigma: 5 });
const outside = window.document.getElementById('outside-btn');
outside.focus();                    // the user moved on before the swap settled
settle();
await flush(10);
ok(window.document.activeElement === outside,
   'C8 restore only ever fills focus the swap dropped');

// ---- C9: the panel scroll position survives ------------------------------
renderPane({ length: 56, amplitude: 0.1, sigma: 5 });
const pane = window.document.getElementById('inspector-pane');
// jsdom reports 0 for every geometry, so give the pane a scrollable shape
Object.defineProperty(pane, 'scrollHeight', { value: 1400, configurable: true });
Object.defineProperty(pane, 'clientHeight', { value: 300, configurable: true });
pane.scrollTop = 310;
inp = inputFor('length');
inp.focus();
inp.value = '60';
fireEnter(inp);
renderPane({ length: 60, amplitude: 0.1, sigma: 5 });
pane.scrollTop = 0;                 // the swap reset the scroll
window.document.body.focus();
settle();
await flush(10);
ok(pane.scrollTop === 310, 'C9 the panel scroll position is restored');

// ---- C10: a real click cancels the pending restore -----------------------
// (commit, then open ANOTHER pulse before the swap settles — focus must not
// land in the new pulse's same-named field)
renderPane({ length: 60, amplitude: 0.1, sigma: 5 });
inp = inputFor('length');
inp.focus();
inp.value = '64';
fireEnter(inp);
window.document.getElementById('outside-btn').dispatchEvent(
    new window.MouseEvent('mousedown', { bubbles: true }));
renderPane({ length: 999, amplitude: 0.2, sigma: 7 });   // a DIFFERENT pulse
window.document.body.focus();
settle();
await flush(10);
ok(window.document.activeElement === window.document.body,
   'C10 a click cancels the pending restore (no focus into another pulse)');

// the click-away commit still records AFTER the click (mousedown → focusout)
renderPane({ length: 999, amplitude: 0.2, sigma: 7 });
inp = inputFor('length');
inp.focus();
inp.value = '1003';
const away = window.document.getElementById('outside-btn');
away.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
submits.length = 0;
fireFocusOut(inp, inp.closest('form').querySelector('.pulse-tune-btn'));
ok(submits.length === 1, 'C10b the click-away commit still fires after the click');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('pulses_commit_selfcheck: all checks passed');
process.exit(0);
})();
