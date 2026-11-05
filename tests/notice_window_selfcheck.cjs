/* Execute the shipped window identity hook without the unrelated page UI. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/app.js'), 'utf8');
const hook = source.slice(0, source.indexOf('}());') + '}());'.length);
let assertions = 0;
function check(value, message) { assert.ok(value, message); assertions++; }
function load() {
    const dom = new JSDOM('', { url: 'http://localhost/', runScripts: 'outside-only' });
    const calls = [];
    dom.window.Headers = Headers;
    dom.window.fetch = function (input, init) { calls.push({ input, init }); return Promise.resolve({}); };
    vm.runInContext(hook, dom.getInternalVMContext());
    function htmx() {
        const detail = { headers: {} };
        dom.window.document.dispatchEvent(new dom.window.CustomEvent('htmx:configRequest', { detail }));
        return detail.headers['X-SM-Window-ID'];
    }
    return { dom, calls, htmx };
}
const first = load(), second = load();
try {
    const id = first.htmx();
    check(typeof id === 'string' && id.length > 10, 'htmx carries a window id');
    check(first.htmx() === id, 'htmx swaps retain the id');
    check(second.htmx() !== id, 'two windows have separate ids');
    first.dom.window.fetch('/state/drift', { headers: { 'X-Test': 'kept' }, cache: 'no-store' });
    check(first.calls[0].init.headers.get('X-SM-Window-ID') === id, 'drift uses the htmx window id');
    check(first.calls[0].init.headers.get('X-Test') === 'kept', 'existing headers survive');
    check(first.calls[0].init.cache === 'no-store', 'fetch options survive');
    first.dom.window.fetch('https://example.com/');
    check(!first.calls[1].init, 'external requests do not carry the id');
    // Reload creates a fresh JS document while retaining the tab's storage.
    const reloaded = load();
    try {
        for (let i = 0; i < first.dom.window.sessionStorage.length; i++) {
            const key = first.dom.window.sessionStorage.key(i);
            reloaded.dom.window.sessionStorage.setItem(key, first.dom.window.sessionStorage.getItem(key));
        }
        // Evaluate after copying storage, as on a browser reload.
        vm.runInContext(hook, reloaded.dom.getInternalVMContext());
        check(reloaded.htmx() !== id, 'a reload cannot inherit the old recipient id');
    } finally { reloaded.dom.window.close(); }
} finally { first.dom.window.close(); second.dom.window.close(); }
console.log(`${assertions} assertions passed`);
