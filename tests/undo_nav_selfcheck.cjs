/* jsdom selfcheck for UndoNav (r16 0-2, docs/73), running the REAL app.js:
 *
 *  U1. visibleEl: visible bulk cell found; display:none column = NOT covered.
 *  U2. ownerSurface mapping: single-qubit -> inspector deep link; multi-
 *      entity -> /bulk; single-pair -> /pair; ports/wiring -> Explorer.
 *  U3. covered path: flash in place, NO navigation.
 *  U4. single-entity elsewhere: inspector-pane load, #table-pane untouched.
 *  U5. multi-entity elsewhere: typing STASHED + one-shot bypass stamp +
 *      /bulk into #table-pane.
 *  U6. restorePass refills the stashed typing into a re-rendered cell.
 *  U7. the real cellsReverted listener drives UndoNav end-to-end.
 *  U8. tray tooltip names the next server-undo target path (+group count).
 *  U9. explorer-owned path routes through _navigateToExplorerPath (/explorer).
 *
 * Run: node tests/undo_nav_selfcheck.cjs  (driven by tests/test_undo_nav.py).
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
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div><div id="pending-tray" data-change-count="2"></div>' +
    '</body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
// jsdom bridges only what we hand it, and `CSS` was never on the list: the
// window HAS a CSS object, but bare `CSS` is undefined here, so app.js's
// `(window.CSS && CSS.escape) ? CSS.escape(s) : s` THREW ReferenceError
// instead of taking either branch. Inside LiveEditUndo._input that throw was
// swallowed by a try/catch returning null, so every cell lookup silently
// missed and whole selfchecks failed for a reason no assertion could name.
// A browser has CSS as a global; the harness must too.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
// real Map-backed storage (UndoNav stash lives in sessionStorage)
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

// jsdom has no layout: shim getClientRects as "visible unless an ancestor is
// display:none / hidden" (the property UndoNav's gate actually cares about).
window.Element.prototype.getClientRects = function () {
    let node = this;
    while (node && node.style) {
        if (node.style.display === 'none' || node.hidden) return [];
        node = node.parentElement;
    }
    return [{}];
};

const ajaxCalls = [];
window.htmx = {
    ajax: function (method, url, opts) { ajaxCalls.push({ method, url, opts }); return Promise.resolve(); },
    trigger: function () {},
    process: function () {},
};
global.htmx = window.htmx;
window.fetch = global.fetch = function (url) {
    // /chip/active-token gates _navigateToExplorerPath — report "loaded" so
    // the Explorer navigation branch runs (U9).
    const payload = String(url).indexOf('/chip/active-token') === 0
        ? { loaded: true } : {};
    return Promise.resolve({ status: 200,
        json: () => Promise.resolve(payload), text: () => Promise.resolve('') });
};
window.confirm = function () { return true; };

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

function mkCell(dp, value, orig) {
    const c = window.document.createElement('input');
    c.className = 'bulk-cell';
    c.setAttribute('data-dot-path', dp);
    c.dataset.dotPath = dp;
    if (orig !== undefined) c.setAttribute('data-orig', orig);
    c.value = value;
    return c;
}

(async function () {
    const U = window.UndoNav;
    ok(!!U, 'UndoNav module exists');

    /* U1 — visibleEl + hidden-column escape */
    const pane = window.document.getElementById('table-pane');
    const cell = mkCell('qubits.q1.f_01', '5.0e9', '5.0e9');
    pane.appendChild(cell);
    ok(U.visibleEl('qubits.q1.f_01') === cell, 'U1: visible bulk cell found');
    const hiddenWrap = window.document.createElement('div');
    hiddenWrap.style.display = 'none';
    const hiddenCell = mkCell('qubits.q1.T1', '100', '100');
    hiddenWrap.appendChild(hiddenCell);
    pane.appendChild(hiddenWrap);
    ok(U.visibleEl('qubits.q1.T1') === null, 'U1: display:none column NOT covered');

    /* U2 — ownerSurface mapping */
    let os = U.ownerSurface([{ dot_path: 'qubits.q2.f_01' }]);
    ok(os.kind === 'inspector' && os.url.indexOf('/qubit/q2?focus=') === 0,
       'U2: single-qubit -> inspector deep link');
    os = U.ownerSurface([{ dot_path: 'qubits.q2.f_01' },
                         { dot_path: 'qubits.q3.f_01' }]);
    ok(os.kind === 'pane' && os.url === '/bulk', 'U2: multi-entity -> /bulk');
    os = U.ownerSurface([{ dot_path: 'qubit_pairs.q1-2.detuning' }]);
    ok(os.kind === 'inspector' && os.url.indexOf('/pair/q1-2?focus=') === 0,
       'U2: single-pair -> pair inspector');
    os = U.ownerSurface([{ dot_path: 'ports.mw_outputs.con1.1.2.band' }]);
    ok(os.kind === 'explorer', 'U2: ports -> Explorer');
    os = U.ownerSurface([{ dot_path: 'wiring.qubits.q1.z.opx_output' }]);
    ok(os.kind === 'explorer', 'U2: wiring -> Explorer');

    /* U3 — covered path flashes, never navigates */
    ajaxCalls.length = 0;
    U.handle([{ dot_path: 'qubits.q1.f_01', old_value_str: '5.0e9' }]);
    ok(ajaxCalls.length === 0, 'U3: covered path -> no navigation');
    ok(cell.classList.contains('leu-flash'), 'U3: covered path -> flash in place');

    /* U4 — single-entity elsewhere -> inspector pane only */
    ajaxCalls.length = 0;
    U.handle([{ dot_path: 'qubits.q9.f_01', old_value_str: '4.9e9' }]);
    ok(ajaxCalls.length === 1
       && ajaxCalls[0].url.indexOf('/qubit/q9?focus=') === 0
       && ajaxCalls[0].opts.target === '#inspector-pane',
       'U4: single-qubit elsewhere -> inspector-pane deep link');
    ok(!window._undoNavAt, 'U4: inspector path never arms the bypass stamp');

    /* U5 — multi-entity elsewhere -> stash + bypass + /bulk */
    cell.value = '5.123e9';                       // user typing, differs from data-orig
    ajaxCalls.length = 0;
    U.handle([{ dot_path: 'qubits.q8.f_01' }, { dot_path: 'qubits.q9.f_01' }]);
    ok(ajaxCalls.length === 1 && ajaxCalls[0].url === '/bulk'
       && ajaxCalls[0].opts.target === '#table-pane',
       'U5: multi-entity elsewhere -> /bulk into #table-pane');
    ok(typeof window._undoNavAt === 'number', 'U5: bypass stamp armed');

    /* U6 — the stash round-trips: refilled into a re-rendered cell, then
       consumed (a later fresh cell must NOT be overwritten again). Asserted
       behaviorally — storage identity differs between jsdom and the eval'd
       scope, so direct getItem probes are unreliable here. */
    pane.removeChild(cell);
    const cell2 = mkCell('qubits.q1.f_01', '5.0e9', '5.0e9');
    pane.appendChild(cell2);
    U.restorePass();
    ok(cell2.value === '5.123e9', 'U5/U6: stashed typing refilled after re-render');
    pane.removeChild(cell2);
    const cell3 = mkCell('qubits.q1.f_01', '5.0e9', '5.0e9');
    pane.appendChild(cell3);
    U.restorePass();
    ok(cell3.value === '5.0e9', 'U6: stash consumed — no phantom refill');

    /* U7 — the real cellsReverted listener drives UndoNav */
    ajaxCalls.length = 0;
    window.document.dispatchEvent(new window.CustomEvent('cellsReverted', {
        detail: { entries: [{ dot_path: 'qubits.q7.f_01', old_value_str: '1' }] } }));
    await flush(10);
    ok(ajaxCalls.some(function (c) { return c.url.indexOf('/qubit/q7?focus=') === 0; }),
       'U7: cellsReverted -> UndoNav navigation end-to-end');

    /* U8 — tray tooltip names the next server-undo target */
    const tray = window.document.getElementById('pending-tray');
    tray.innerHTML =
        '<div class="tray-change-item" data-group-id="g1">' +
        '<code class="tray-change-path">qubits.q1.T1</code></div>' +
        '<div class="tray-change-item" data-group-id="g2">' +
        '<code class="tray-change-path">qubits.q1.f_01</code></div>' +
        '<div class="tray-change-item" data-group-id="g2">' +
        '<code class="tray-change-path">qubits.q2.f_01</code></div>';
    const btn = window.document.createElement('button');
    window.LiveEditUndo.refreshTip(btn);
    ok(btn.title.indexOf('qubits.q2.f_01') >= 0, 'U8: tooltip names the target path');
    ok(btn.title.indexOf('+1 more') >= 0, 'U8: tooltip counts the group mates');

    /* U9 — explorer-owned path routes through the Explorer navigation */
    ajaxCalls.length = 0;
    U.handle([{ dot_path: 'ports.mw_outputs.con1.1.2.band' }]);
    await flush(10);
    ok(ajaxCalls.some(function (c) { return String(c.url).indexOf('/explorer') === 0; }),
       'U9: ports path -> Explorer navigation');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('undo_nav_selfcheck: all checks passed');
    process.exit(0);   // app.js pollers (setInterval) keep node alive otherwise
})().catch(function (e) { console.error('UNCAUGHT:', e && e.stack || e); process.exit(1); });
