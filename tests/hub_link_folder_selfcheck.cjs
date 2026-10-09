'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const css = fs.readFileSync(path.join(root, 'style.css'), 'utf8');
const modalZ = Number(css.match(/#hub-folder-modal\s*\{[^}]*z-index:\s*(\d+)/)[1]);
const drawerZ = Number(css.match(/\.field-history-panel\s*\{[^}]*z-index:\s*(\d+)/)[1]);
assert(modalZ > drawerZ);
assert(/#hub-folder-body\s*\{[^}]*overflow:\s*auto/.test(css));
const dom = new JSDOM('<body><button id="opener">Open</button>' +
    '<div id="topo-trends"><div class="hub-notes" data-hub-url="/topology/trends?metric=T1"></div></div>' +
    '<div id="param-history-root"><div class="hub-notes" data-hub-url="/param-history"></div></div>' +
    '<div id="param-history-drawer"><div class="hub-notes" data-hub-url="/param-history/expand?qubit=qA1&amp;prop=T1"></div></div>' +
    '<div id="inspector-pane"><div class="hub-notes" data-hub-url="/field/history?path=qubits.qA1.T1"></div></div>' +
    '<div id="state-version-panel"><div class="hub-notes" data-hub-url="/state/versions"></div></div>' +
    '<div id="state-history-body"><div class="hub-notes" data-hub-url="/state-history?body=1"></div></div>' +
    '<div id="field-history-panel"><div class="hub-notes" data-hub-url="/field/history"></div></div>' +
    '<div class="colhist-overlay"><div class="hub-notes" data-hub-url="/bulk/column-history"></div></div></body>',
    {url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window;
w.eval(app.slice(app.indexOf('function _focusableIn('), app.indexOf('/**\n * Show a transient toast')));
const calls = [];
w.htmx = {ajax: (method, url, opts) => { calls.push({method, url, opts}); return Promise.resolve(); }, process: () => {}};
w.eval(fs.readFileSync(path.join(root, 'hub-folders.js'), 'utf8'));
const opener = w.document.getElementById('opener');
// jsdom has no layout; focusability reads client rects.
Object.defineProperty(w.HTMLElement.prototype, 'offsetWidth', {get: () => 10});
opener.focus();
let outerEscapes = 0;
const outerRelease = w.trapFocus(w.document.getElementById('state-version-panel'), () => outerEscapes++);
w.HubFolders.open(opener);
let modal = w.document.getElementById('hub-folder-modal');
assert(modal.querySelector('[role="dialog"][aria-modal="true"][aria-labelledby="hub-folder-title"]'));
const close = modal.querySelector('button');
// S10 walk: the close control is the header's x beside the title, not a full-width bar
assert(close.classList.contains('ch-close') && close.textContent === '\u00d7', close.outerHTML);
assert(close.parentElement.classList.contains('ch-head') && close.parentElement.querySelector('#hub-folder-title'));
assert(!/>Close</.test(modal.innerHTML), 'no Close bar');
const link = w.document.createElement('button');
link.textContent = 'Link';
modal.querySelector('#hub-folder-body').appendChild(link);
link.focus();
w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true}));
assert.strictEqual(w.document.activeElement, close);
w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}));
assert.strictEqual(w.document.activeElement, link);
w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
assert(!w.document.getElementById('hub-folder-modal'));
assert.strictEqual(outerEscapes, 0);
assert.strictEqual(w.document.activeElement, opener);
outerRelease();
opener.focus();
w.HubFolders.open(opener);
modal = w.document.getElementById('hub-folder-modal');
w.document.dispatchEvent(new w.CustomEvent('htmx:responseError', {detail: {
    elt: modal.querySelector('button'), xhr: {responseText: 'The open chip changed.'}}}));
assert.strictEqual(modal.querySelector('#hub-folder-body').textContent, 'The open chip changed.');
w.document.dispatchEvent(new w.CustomEvent('hubLinked'));
assert(!w.document.getElementById('hub-folder-modal'));
assert.strictEqual(calls.length, 4);
assert.deepStrictEqual(calls.map(c => c.url), ['/topology/trends?metric=T1', '/param-history',
    '/param-history/expand?qubit=qA1&prop=T1', '/field/history?path=qubits.qA1.T1']);
assert(calls.every(c => c.method === 'GET'));
assert.deepStrictEqual(calls.map(c => c.opts.swap), ['outerHTML', 'outerHTML', 'innerHTML', 'innerHTML']);
const templates = path.join(root, '..', 'templates');
assert(fs.readFileSync(path.join(templates, '_state_history.html'), 'utf8').includes('hubLinked from:body'));
assert(fs.readFileSync(path.join(templates, 'base.html'), 'utf8').includes("asset_url('hub-folders.js')"));
(async function () {
    const fetched = [];
    w.fetch = (url, opts) => {
        fetched.push({url, opts});
        return Promise.resolve({text: () => Promise.resolve('<p>History ready.</p>')});
    };
    w.safeLSGet = () => null;
    w.document.getElementById('field-history-panel').remove();
    w.document.querySelector('.colhist-overlay').remove();
    for (const name of ['FieldHistory', 'ColumnHistory', 'StateVersions']) {
        const start = app.indexOf('window.' + name + ' = (function () {');
        w.eval(app.slice(start, app.indexOf('\n})();', start) + 6));
    }
    w.FieldHistory.open(opener, 'qubits.qA1.T1', null);
    const table = w.document.createElement('table');
    table.innerHTML = '<thead><tr><th data-col-key="c"><button data-grid="qubit">History</button></th></tr></thead>' +
        '<tbody><tr data-qubit="qA1"><td data-col-key="c"><input class="bulk-cell" data-dot-path="qubits.qA1.T1"></td></tr></tbody>';
    w.document.body.appendChild(table);
    w.ColumnHistory.open(table.querySelector('button'));
    await new Promise(resolve => setTimeout(resolve, 50));
    const before = fetched.length;
    w.HubFolders.open(opener);
    w.document.dispatchEvent(new w.CustomEvent('hubLinked'));
    await new Promise(resolve => setTimeout(resolve, 1100));
    assert.strictEqual(fetched.length, before + 2);
    assert(fetched.slice(before).some(c => c.url === '/field/history?path=qubits.qA1.T1'));
    assert(fetched.slice(before).some(c => c.url === '/bulk/column-history' && c.opts.body.includes('paths=')));
    // S10 C6: '/state/versions?changes=...' -> '/state/versions', the changes-only filter is gone
    assert(calls.some(c => c.url === '/state/versions'));
    console.log('Folder modal focus, errors and surface refresh: passed');
    w.close();
})().catch(error => { console.error(error); w.close(); process.exitCode = 1; });
