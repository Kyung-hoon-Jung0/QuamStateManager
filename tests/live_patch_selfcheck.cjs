/* jsdom selfcheck for LiveSurfacePatch (customer 2026-08-27, critical): a
 * sync pull must leave the page where it is and change only the VALUES.
 * Pins, against the real app.js:
 *   1. changes are handed to the grids' own revertPaths (qubit + pair) verbatim
 *   2. a rendered Json-tree leaf is rewritten in place (text, edit value, kind
 *      class) through the tree's own formatter
 *   3. a leaf under a COLLAPSED subtree patches the lazy snapshot, so a later
 *      expand shows the pulled value, not the pre-pull one
 *   4. All-values inputs + inspector value inputs are patched
 *   5. _patchOrRefreshLiveSurface: non-structural → patch, NO page re-GET;
 *      structural → wholesale refresh (re-GET issued) with the scroll carried
 * Run: node tests/live_patch_selfcheck.cjs   (driven by tests/test_live_patch.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body><div id="table-pane" style="overflow:auto">' +
    '<div id="explorer-tree-state"></div>' +
    '<div class="tree-node" data-path="qubits.q1.T1"><span class="tree-row"><span class="tree-key">T1</span>' +
    '<span class="tree-val tree-val-number">1e-05</span></span></div>' +
    '<div class="tree-node" data-path="qubits.q2"><span class="tree-row"><span class="tree-key">q2</span></span></div>' +
    '<input class="av-input" data-dot-path="qubits.q1.T2" data-orig="2e-05" value="2e-05">' +
    '<form><input type="hidden" name="dot_path" value="qubits.q1.T2"><input name="value" value="2e-05"></form>' +
    '</div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage; window.localStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
const ajax = [];
window.htmx = { ajax: (m, u, o) => { ajax.push(u); return Promise.resolve(); }, trigger: () => {}, process: () => {} };
global.htmx = window.htmx;
const gridCalls = [];
window.BulkEdit = { revertPaths: (entries) => { gridCalls.push(['qubit', entries]); return { patched: entries.length, missing: 0 }; } };
window.BulkPairEdit = { revertPaths: (entries) => { gridCalls.push(['pair', entries]); return { patched: 0, missing: 0 }; } };

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
const d = window.document;
ok(typeof window._treeFormatValue === 'function', 'the tree exports its own value formatter');
// a collapsed subtree with a lazy snapshot
const q2 = d.querySelector('.tree-node[data-path="qubits.q2"]');
q2._lazyData = { value: { T1: 3e-5, xy: { operations: { x180: { amplitude: 0.1 } } } }, type: 'object', path: 'qubits.q2' };

const changes = [
    { dot_path: 'qubits.q1.T1', old_value_disp: '0.000011', old_value_str: '1.1e-05', old_kind: 'num', value: 1.1e-5 },
    { dot_path: 'qubits.q1.T2', old_value_disp: '0.000025', old_value_str: '2.5e-05', old_kind: 'num', value: 2.5e-5 },
    { dot_path: 'qubits.q2.xy.operations.x180.amplitude', old_value_disp: '0.2', old_value_str: '0.2', old_kind: 'num', value: 0.2 },
    { dot_path: 'qubits.q1.name', old_value_disp: 'foo', old_value_str: 'foo', old_kind: 'str', value: 'foo' },
];
const res = window.LiveSurfacePatch.apply(changes);
// 1. grids get the payload verbatim
ok(gridCalls.length === 2 && gridCalls[0][1] === changes && gridCalls[1][1] === changes,
   'both grids receive the change list verbatim (revertPaths shape)');
// 2. rendered leaf
const leaf = d.querySelector('.tree-node[data-path="qubits.q1.T1"] .tree-val');
ok(leaf.textContent === window._treeFormatValue(1.1e-5), 'a rendered tree leaf is rewritten through the tree formatter (' + leaf.textContent + ')');
ok(leaf.classList.contains('tree-val-number') && leaf.dataset.editVal === window._treeFormatValue(1.1e-5), 'leaf kind class + edit value follow');
// 3. lazy snapshot under a collapsed node
ok(q2._lazyData.value.xy.operations.x180.amplitude === 0.2, 'a leaf under a COLLAPSED subtree patches the lazy snapshot');
ok(q2._lazyData.value.T1 === 3e-5, 'untouched lazy leaves stay');
// 4. inputs
ok(d.querySelector('.av-input').value === '0.000025' && d.querySelector('.av-input').getAttribute('data-orig') === '0.000025',
   'All-values input + its baseline patched');
ok(d.querySelector('form input[name="value"]').value === '2.5e-05', 'inspector value input patched');
ok(res.tree >= 2 && res.inputs >= 2, 'the patch reports what it reached (tree ' + res.tree + ', inputs ' + res.inputs + ')');
// 5. dispatcher: non-structural → no re-GET; structural → re-GET with scroll kept
ajax.length = 0;
const pane = d.getElementById('table-pane');
ok(window._patchOrRefreshLiveSurface({ changes: changes, structural: false }) === 'patched' && ajax.length === 0,
   'non-structural sync patches in place — NO page re-GET');
ok(window._patchOrRefreshLiveSurface({ changes: [], structural: true }) === 'refreshed' && ajax.length === 1 && /explorer/.test(ajax[0]),
   'structural sync falls back to the wholesale refresh (' + ajax.join(',') + ')');
ok(window._patchOrRefreshLiveSurface({}) === 'refreshed', 'a response without a patch (older server) refreshes as before');
process.exit(fails ? 1 : 0);
