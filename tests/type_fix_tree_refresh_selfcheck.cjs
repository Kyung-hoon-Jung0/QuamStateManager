/* jsdom selfcheck, QA F-E: after "Fix N values..." converts stored-as-text
 * numbers, an OPEN Explorer tree must show the stored number (not the old
 * quoted text) and lose the stale warning mark -- without a reload, and
 * without re-opening branches the user collapsed. Against the REAL app.js:
 *
 *  1. typeFixApply patches each converted leaf in place from the route's
 *     `changes` list (text, kind class) and tints the row pending.
 *  2. diagnostics-changed re-applies the Explorer marks: a finding that is
 *     gone loses its warning icon + row class.
 *  3. that re-mark never expands a collapsed branch (noExpand), even when a
 *     remaining finding lives under it.
 *
 * Run: node tests/type_fix_tree_refresh_selfcheck.cjs
 *      (driven by tests/test_type_autofix.py::test_type_fix_tree_refresh_selfcheck)
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 10); }); }

const TREE =
    '<div id="explorer-tree-state" class="json-tree">' +
    '<div class="tree-node" data-path="qubits"><div class="tree-row"><span class="tree-toggle"></span>' +
    '<span class="tree-key">qubits</span></div>' +
    '<div class="tree-node" data-path="qubits.q4"><div class="tree-row"><span class="tree-toggle"></span>' +
    '<span class="tree-key">q4</span></div>' +
    '<div class="tree-node" data-path="qubits.q4.T1"><div class="tree-row tree-row-warn">' +
    '<span class="tree-key">T1</span><span class="tree-val tree-val-string">"1.2288957890063396e-05"</span>' +
    '<span class="tree-warn-icon" title="stored as TEXT">⚠</span></div></div>' +
    '</div>' +
    '<div class="tree-node" data-path="qubits.q5"><div class="tree-row">' +
    '<span class="tree-toggle collapsed"></span><span class="tree-key">q5</span></div></div>' +
    '</div></div>' +
    '<div id="explorer-tree-wiring" class="json-tree"></div>';

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane">' + TREE + '</div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div><div id="pending-tray"></div>' +
    '<div class="tfx-card" data-sig="S"><input type="checkbox" class="tfx-pick" checked' +
    ' data-path="qubits.q4.T1"><button id="tfx-apply">Convert</button>' +
    '<div class="tfx-error" hidden></div></div>' +
    '</body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.URLSearchParams = window.URLSearchParams;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
// htmx.trigger dispatches a bubbling CustomEvent in a real browser -- the new
// listener rides on exactly that, so the stub must do the same.
window.htmx = {
    ajax: function () { return Promise.resolve(); },
    trigger: function (el, name) { el.dispatchEvent(new window.CustomEvent(name, { bubbles: true })); },
    process: function () {},
};
global.htmx = window.htmx;

let findings = { value_spec: [
    { jump_path: 'qubits.q4.T1', message: 'stored as TEXT' },
    { jump_path: 'qubits.q5.T1', message: 'still bad' },
], connectivity: [] };
window.fetch = global.fetch = function (url) {
    const u = String(url);
    if (u.indexOf('/type-fix/apply') === 0) {
        return Promise.resolve({ status: 200, json: () => Promise.resolve({
            ok: true, count: 1, tray_html: '<div></div>',
            changes: [{ dot_path: 'qubits.q4.T1', value: 1.2288957890063396e-05,
                        old_value_str: '1.228896e-05', old_value_disp: '0.000012288957890063396',
                        old_kind: 'num', created: false, deleted: false, source_file: 'state' }],
        }) });
    }
    if (u.indexOf('/diagnostics/findings.json') === 0) {
        return Promise.resolve({ status: 200, json: () => Promise.resolve(findings) });
    }
    return Promise.resolve({ status: 200, text: () => Promise.resolve(''), json: () => Promise.resolve({}) });
};

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window._swapPendingTray = function () {};
const d = window.document;

let q5Clicks = 0;
d.querySelector('.tree-node[data-path="qubits.q5"] .tree-toggle')
    .addEventListener('click', function () { q5Clicks++; });

(async function () {
    await flush(50);   // let the on-load mark pass settle before measuring
    q5Clicks = 0;

    /* 1. the converted leaf is patched in place */
    findings = { value_spec: [{ jump_path: 'qubits.q5.T1', message: 'still bad' }], connectivity: [] };
    window.typeFixApply(d.getElementById('tfx-apply'));
    await flush(30);
    const val = d.querySelector('.tree-node[data-path="qubits.q4.T1"] .tree-val');
    ok(val.textContent === window._treeFormatValue(1.2288957890063396e-05),
       'the tree leaf shows the stored NUMBER (' + val.textContent + ')');
    ok(val.classList.contains('tree-val-number') && !val.classList.contains('tree-val-string'),
       'the leaf kind class is number, not string');
    ok(d.querySelector('.tree-node[data-path="qubits.q4.T1"] > .tree-row').classList.contains('tree-row-pending'),
       'the converted row is tinted pending (an unapplied tray change)');

    /* 2. the stale warning mark goes once diagnostics-changed lands */
    await flush(1000);
    const row = d.querySelector('.tree-node[data-path="qubits.q4.T1"] > .tree-row');
    ok(!row.querySelector('.tree-warn-icon'), 'the stale warning icon is gone');
    ok(!row.classList.contains('tree-row-warn'), 'the stale warning row class is gone');

    /* 3. ...and a remaining finding under a COLLAPSED branch is not expanded */
    ok(q5Clicks === 0, 'the re-mark never re-opens a collapsed branch (clicks: ' + q5Clicks + ')');

    /* 2b. a plain tree edit (any mutation) re-marks too: a new finding appears */
    findings = { value_spec: [{ jump_path: 'qubits.q4.T1', message: 'new' }], connectivity: [] };
    window.htmx.trigger(d.body, 'diagnostics-changed');
    await flush(700);
    ok(!!d.querySelector('.tree-node[data-path="qubits.q4.T1"] > .tree-row .tree-warn-icon'),
       'a finding that appears after an edit is marked without a reload');
    ok(q5Clicks === 0, 'still no expansion');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('ALL OK');
    process.exit(0);
})();
