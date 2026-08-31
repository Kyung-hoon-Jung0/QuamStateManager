/* docs/145 -- Json Tree inline edit, two customer items:
 *  1. a STRING edits as its JSON literal: the file says "direct", the editor
 *     shows "direct" (quotes included); committing a quoted literal unwraps
 *     it, so the server receives the raw value; unquoted text degrades to
 *     the old behavior (sent as typed).
 *  2. while editing, the hover row actions + ? help are hidden (the async
 *     type chip used to paint straight under them), and the editing value
 *     box is inline-flex so the chip widens it instead of overflowing.
 * Run: node tests/tree_edit_literal_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div class="explorer-pane">'
    + '<div id="explorer-tree-state" class="json-tree"></div></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
// realm-installed fetch mock (docs/125 rule + its docs/144 dual): capture
// /field/edit bodies, answer /field/peek with an expected-type payload
window.__fetchLog = [];
window.eval("fetch = window.fetch = function (url, opts) {" +
    " window.__fetchLog.push({ url: String(url), body: opts && opts.body ? String(opts.body) : null });" +
    " if (String(url).indexOf('/field/peek') === 0) return Promise.resolve({ json: function () {" +
    "   return Promise.resolve({ expected: {} }); } });" +
    " return Promise.resolve({ json: function () { return Promise.resolve(" +
    "   { ok: true, stored: 'smooth', formatted: 'smooth' }); } });" +
    "};");
global.fetch = window.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
window.renderJsonTree('explorer-tree-state', {
    qubits: { q1: { gate_shape: 'direct', f_01: 4.5e9, esc: 'a"b' } },
}, { defaultDepth: 8, crud: true });

(async () => {
    // leaves materialize through the search pipeline (same as the cap harness)
    window.jsonTreeSearch('explorer-tree-state', 'q1');
    await new Promise((r) => setTimeout(r, 300));   // past the 200ms debounce
    const val = d.querySelector('.tree-node[data-path="qubits.q1.gate_shape"] .tree-val');
    ok(!!val && val.classList.contains('tree-val-string'), 'string leaf rendered');
    val.click();
    await new Promise((r) => setTimeout(r, 30));
    const input = val.querySelector('input.tree-edit-input');
    ok(!!input, 'clicking the value opens the inline editor');
    ok(input.value === '"direct"', 'the editor shows the JSON literal, quotes included (' + input.value + ')');

    // commit a QUOTED literal -> the POSTed value is unwrapped
    input.value = '"smooth"';
    input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const edit = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).pop();
    ok(!!edit, 'commit POSTs /field/edit');
    ok(edit && /(^|&)value=smooth(&|$)/.test(edit.body),
       'a quoted literal is UNWRAPPED before sending (' + (edit && edit.body) + ')');

    // a string with an inner quote round-trips through JSON escaping
    const esc = d.querySelector('.tree-node[data-path="qubits.q1.esc"] .tree-val');
    esc.click();
    await new Promise((r) => setTimeout(r, 30));
    const einp = esc.querySelector('input.tree-edit-input');
    ok(einp && einp.value === JSON.stringify('a"b'),
       'inner quotes are escaped exactly as the file spells them (' + (einp && einp.value) + ')');
    einp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    // a NUMBER edits exactly as before (no quoting)
    const nval = d.querySelector('.tree-node[data-path="qubits.q1.f_01"] .tree-val');
    nval.click();
    await new Promise((r) => setTimeout(r, 30));
    const ninp = nval.querySelector('input.tree-edit-input');
    ok(ninp && ninp.value.charAt(0) !== '"', 'a number edits unquoted (' + (ninp && ninp.value) + ')');
    ninp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    // unquoted text still goes through as typed (old behavior kept)
    const val2 = d.querySelector('.tree-node[data-path="qubits.q1.gate_shape"] .tree-val');
    val2.click();
    await new Promise((r) => setTimeout(r, 30));
    const inp2 = val2.querySelector('input.tree-edit-input');
    inp2.value = 'bare_text';
    inp2.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const edit2 = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).pop();
    ok(edit2 && /(^|&)value=bare_text(&|$)/.test(edit2.body),
       'unquoted text is sent as typed (' + (edit2 && edit2.body) + ')');

    // 2. the overlap fix is a stylesheet contract -- pin the rules
    const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
    ok(/\.tree-row:has\(\.tree-val-editing\)[^{]*\.tree-row-actions/.test(css)
       && css.indexOf('display: none') > -1,
       'editing hides the hover row actions (the chip used to paint under them)');
    ok(/\.tree-val-editing \{[^}]*inline-flex/.test(css),
       'the editing value box is inline-flex so the async chip widens it');

    console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (10 assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e && e.message); process.exit(1); });
