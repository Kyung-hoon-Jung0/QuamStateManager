/* jsdom selfcheck: Ctrl+Z / Ctrl+Shift+Z on the Json tree and on an inline
 * (Pulses / inspector) field -- the three defects a real-Chrome check found
 * on 2026-08-28 after the night session shipped the Undo trail:
 *   1. the tree's data model (container._treeData) did not follow an inline
 *      edit, an undo or a redo -- the DOM was repainted, but the next expand
 *      or search served the value from BEFORE the edit
 *   2. an inline field reverted by undo kept its edited data-committed, so
 *      the next click-away RE-COMMITTED the reverted value as a new edit
 *      (which cleared the redo stack: Ctrl+Shift+Z on Pulses did nothing)
 *   3. cellsReverted auto-navigated to the "owning surface" -- on Pulses a
 *      redo of an off-screen field replaced the pulse inspector with a qubit
 *      inspector. Now: flash what is visible, and only the trail's button
 *      navigates.
 * Run: node tests/undo_pages_selfcheck.cjs   (driven by tests/test_undo_trail.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body>'
    + '<div class="explorer-pane"><div id="explorer-tree-state" class="json-tree"></div></div>'
    + '<div id="pending-tray"></div>'
    + '<div id="inspector-pane"><form class="inline-edit pulse-edit-form" hx-post="/pulse/edit">'
    + '<input type="hidden" name="dot_path" value="qubits.q1.xy.operations.saturation.amplitude">'
    + '<input type="text" name="value" class="edit-input" data-param="amplitude" data-committed="0.028" value="0.028"></form></div>'
    + '</body></html>', { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
// fetch stub: /field/edit commits, /field/peek answers the reverted value
const fetches = [];
global.fetch = (url, opts) => {
    fetches.push(String(url));
    if (String(url).indexOf('/field/edit') === 0) return Promise.resolve({ json: () => Promise.resolve({ ok: true, stored: 0.77, stored_kind: 'num' }) });
    if (String(url).indexOf('/field/peek') === 0) { const dp = decodeURIComponent(String(url).split('dot_path=')[1] || ''); const vals = {}; vals[dp] = 0.1; return Promise.resolve({ json: () => Promise.resolve({ ok: true, values: vals }) }); }
    return new Promise(() => {});
};
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.openConfigManual = function () {};
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
global.SearchQuery = window.SearchQuery;

const d = window.document;
const c = d.getElementById('explorer-tree-state');
const DATA = { qubits: { q1: { amp_0: 0.5, T1: 2e-5 }, q2: { amp_0: 0.6 }, q3: { zeta: 0.9 } } };   // q3: never expanded, never searched
window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 3, crud: true });
c.querySelector('.tree-node[data-path="qubits.q1"] .tree-toggle').click();   // materialise q1's leaves

// ── 1. the tree model follows an inline edit ─────────────────────────────
const valEl = c.querySelector('.tree-node[data-path="qubits.q1.amp_0"] .tree-val');
ok(!!valEl, 'fixture: the leaf is rendered');
valEl.click();
const inp = c.querySelector('.tree-node[data-path="qubits.q1.amp_0"] input');
ok(!!inp, 'clicking the value opens the inline editor');
inp.value = '0.77';
inp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
setTimeout(function () {
    ok(fetches.some((u) => u.indexOf('/field/edit') === 0), 'Enter posted /field/edit');
    ok(c._treeData.qubits.q1.amp_0 === 0.77, 'the data model carries the edited value (was: the value before the edit)');
    ok(c._flatIndex === null || c._flatIndex === undefined, 'and the flat search index is dropped for a rebuild');
    window.jsonTreeSearch('explorer-tree-state', 'amp_0');
    setTimeout(function () {
        const hit = (c._flatIndex.flat || []).filter((e) => e.path === 'qubits.q1.amp_0')[0];
        ok(!!hit && hit.val === '0.77', 'search sees the edited value (' + (hit && hit.val) + ')');
        window.jsonTreeSearch('explorer-tree-state', '');

        // ── 1b. undo (server tier) repaints the node AND the model ────────
        window._revertTreeNode('qubits.q1.amp_0', '0.1');
        setTimeout(function () {
            ok(c.querySelector('.tree-node[data-path="qubits.q1.amp_0"] .tree-val').textContent === '0.1', 'undo repainted the node in place');
            ok(c._treeData.qubits.q1.amp_0 === 0.1, 'and the data model follows the undo');
            // a leaf inside a NEVER-expanded branch (q2 is collapsed): no node, the model must still follow
            ok(!c.querySelector('.tree-node[data-path="qubits.q3.zeta"]'), 'fixture: q3.zeta was never materialised');
            window._revertTreeNode('qubits.q3.zeta', '0.1');
            setTimeout(function () {
                ok(c._treeData.qubits.q3.zeta === 0.1, 'an undo over a never-expanded leaf still updates the model (was 0.9)');
                c.querySelector('.tree-node[data-path="qubits.q3"] .tree-toggle').click();
                const v2 = c.querySelector('.tree-node[data-path="qubits.q3.zeta"] .tree-val');
                ok(v2 && v2.textContent === '0.1', 'and expanding it shows the reverted value');
            }, 30);

            // ── 2. inline (Pulses) field: reverted as a COMMITTED value ────
            const f = d.querySelector('#inspector-pane input[name="value"]');
            let inputEvents = 0; f.addEventListener('input', function () { inputEvents++; });
            let submits = 0; f.form.requestSubmit = function () { submits++; };
            d.dispatchEvent(new CustomEvent('cellsReverted', { detail: { message: 'Undone', entries: [
                { dot_path: 'qubits.q1.xy.operations.saturation.amplitude', old_value_str: '0.014', old_value_disp: '0.014', old_kind: 'num' }] } }));
            ok(f.value === '0.014', 'the field shows the reverted value');
            ok(f.getAttribute('data-committed') === '0.014', 'data-committed follows it (the baseline the click-away commit compares against)');
            ok(inputEvents === 1, "an 'input' event lets the field's own listeners (Pulses preview) follow");
            // the click-away that used to re-commit the reverted value
            f.focus(); f.dispatchEvent(new window.FocusEvent('focusout', { bubbles: true, relatedTarget: d.body }));
            ok(submits === 0, 'a click-away after the undo does NOT re-commit (value == baseline)');

            // ── 3. no automatic navigation ─────────────────────────────────
            let handled = 0, flashed = 0;
            const realHandle = window.UndoNav.handle;
            window.UndoNav.handle = function () { handled++; };
            ok(typeof window.UndoNav.flashVisible === 'function', 'UndoNav.flashVisible exists');
            d.dispatchEvent(new CustomEvent('cellsReverted', { detail: { message: 'Redone', entries: [
                { dot_path: 'qubits.q9.not_on_this_page', old_value_str: '1', old_value_disp: '1', old_kind: 'num' }] } }));
            ok(handled === 0, 'an off-screen entry does NOT auto-navigate (the pulse inspector stays)');
            ok(d.getElementById('inspector-pane').querySelector('form.pulse-edit-form') !== null, 'the inspector pane is untouched');
            window.UndoNav.handle = realHandle;
            // the trail's button is what navigates, on the press
            window.eval(fs.readFileSync(path.join(STATIC, 'undo-trail.js'), 'utf8'));
            let navved = [];
            window.UndoNav.handle = function (entries) { navved.push(entries[0].dot_path); };
            window.UndoTrail.goTo('qubits.q9.not_on_this_page');
            ok(navved[0] === 'qubits.q9.not_on_this_page', 'go to field hands an off-screen path to UndoNav.handle -- on the press');
            window.UndoNav.handle = realHandle;
            // ── 4. on the Pulses page a pulse parameter goes to the PULSE detail ──
            //    (user report: "go to field" on an undone pulse length opened the
            //    qubit inspector, with no graph)
            const os0 = window.UndoNav.ownerSurface([{ dot_path: 'qubits.q1.xy.operations.saturation.length' }]);
            ok(os0.kind === 'inspector' && /\/qubit\/q1/.test(os0.url), 'off the Pulses page a qubit-owned path still opens the qubit inspector');
            const rows = d.createElement('div'); rows.id = 'pulses-rows-wrap'; d.body.appendChild(rows);
            const os1 = window.UndoNav.ownerSurface([{ dot_path: 'qubits.q1.xy.operations.saturation.length' }]);
            ok(os1.kind === 'pulse' && os1.url === '/pulse/detail?path=' + encodeURIComponent('qubits.q1.xy.operations.saturation'),
               'on the Pulses page the same path opens the pulse detail (' + os1.url + ')');
            const os2 = window.UndoNav.ownerSurface([{ dot_path: 'qubit_pairs.q1-q2.macros.cz.flux_pulse_length' }]);
            ok(os2.kind === 'pulse' && /qubit_pairs\.q1-q2\.macros\.cz$/.test(decodeURIComponent(os2.url.split('path=')[1])), 'a pair macro parameter too');
            const os3 = window.UndoNav.ownerSurface([{ dot_path: 'qubits.q1.T1' }]);
            ok(os3.kind === 'inspector', 'a non-pulse path keeps the inspector');
            const ajaxed = [];
            window.htmx.ajax = (m, u, o) => { ajaxed.push(m + ' ' + u + ' -> ' + (o && o.target)); return Promise.resolve(); };
            window.UndoNav.handle([{ dot_path: 'qubits.q1.xy.operations.saturation.length' }]);
            ok(ajaxed.length === 1 && /^GET \/pulse\/detail\?path=.* -> #inspector-pane$/.test(ajaxed[0]), 'handle() opens it in the inspector pane (' + ajaxed[0] + ')');
            rows.remove();
            setTimeout(function () { process.exit(fails ? 1 : 0); }, 120);   // after the never-expanded-leaf pins above
        }, 50);
    }, 300);
}, 50);
