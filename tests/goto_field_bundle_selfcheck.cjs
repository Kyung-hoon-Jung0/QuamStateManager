/* F25 (final walk): Diagnostics "Go to field" into the Json Tree.
 * htmx 2 resolves htmx.ajax's promise BEFORE any swap when Bundles'
 * htmx:confirm holds the request (the cm6 bundle /explorer needs is not
 * loaded yet). The jump must wait for the held request and the pane, land
 * the highlight on the field, and never show a false error toast.
 *  A: the pane arrives late (bundle held ~7 s): highlight, no warning toast.
 *  B: idle jump with a search already in the box and a POINTER leaf on the
 *     path: the highlight must survive the search clear + re-render.
 * Run: node tests/goto_field_bundle_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const JUMP = 'qubit_pairs.p1.macros.cz_bipolar.flux_pulse_qubit';
const STATE = {
    qubits: { q1: { z: { operations: { cz_flux: { length: 52, amplitude: 0.1 } } } } },
    qubit_pairs: { p1: { macros: { cz_bipolar: {
        flux_pulse_qubit: '#/qubits/q1/z/operations/cz_flux', phase_shift_control: 0.1 } } } },
};

function world(opts) {
    const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
        + '<div id="table-pane"><p>diagnostics</p></div></body></html>',
        { url: 'http://localhost/diagnostics', pretendToBeVisual: true });
    const { window } = dom;
    global.window = window; global.document = window.document; global.CSS = window.CSS;
    global.getComputedStyle = window.getComputedStyle.bind(window);
    global.Event = window.Event; global.CustomEvent = window.CustomEvent;
    global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
    global.navigator = window.navigator; global.location = window.location;
    const mem = {};
    global.localStorage = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } };
    window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
    global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
    global.MutationObserver = window.MutationObserver;
    global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
    global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
    window.Element.prototype.scrollIntoView = function () {};
    function landPane() {
        const pane = window.document.getElementById('table-pane');
        pane.innerHTML = '<input id="explorer-search" type="search" value="' + (opts.search || '') + '"'
            + ' oninput="if(window.jsonTreeSearch)jsonTreeSearch(this.value)">'
            + '<div id="explorer-tree-state" class="json-tree"></div>';
        window.renderJsonTree('explorer-tree-state', STATE, { defaultDepth: 1, crud: true });
    }
    window.htmx = { ajax: function () { setTimeout(landPane, opts.paneMs); return Promise.resolve(); },
                    trigger: () => {}, process: () => {} };
    global.htmx = window.htmx;
    window.eval("fetch = window.fetch = function (url) {" +
        " return Promise.resolve({ ok: true, json: function () { return Promise.resolve(" +
        "   String(url).indexOf('/chip/active-token') === 0 ? { loaded: true } : {}); } });" +
        "};");
    global.fetch = window.fetch;
    window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    const held = new Promise((r) => setTimeout(r, opts.pendingMs));
    window.Bundles = window.Bundles || {};
    window.Bundles.pending = function () { return held; };
    return window;
}

function toasts(window) {
    return Array.from(window.document.querySelectorAll('#status-bar .toast-warning')).map((t) => t.textContent);
}

async function caseA() {
    const window = world({ paneMs: 7000, pendingMs: 6900 });
    const btn = window.document.createElement('button');
    btn.setAttribute('data-jump-path', JUMP);
    window.goToDiagField(btn);
    await new Promise((r) => setTimeout(r, 8200));
    const node = window.document.querySelector('#explorer-tree-state .tree-node[data-path="' + JUMP + '"]');
    ok(toasts(window).length === 0, 'A: no warning toast while the held /explorer request lands (got: ' + toasts(window).join(' | ') + ')');
    ok(node && node.classList.contains('tree-highlight'), 'A: the field row is highlighted after a late pane');
}

async function caseB() {
    const window = world({ paneMs: 30, pendingMs: 0, search: 'phase' });
    const btn = window.document.createElement('button');
    btn.setAttribute('data-jump-path', JUMP);
    window.goToDiagField(btn);
    await new Promise((r) => setTimeout(r, 1500));
    const c = window.document.getElementById('explorer-tree-state');
    const node = c && c.querySelector('.tree-node[data-path="' + JUMP + '"]');
    const hl = c && c.querySelector('.tree-highlight');
    console.log('B info: highlighted=' + (hl && hl.getAttribute('data-path')) + ' targetExists=' + !!node
        + ' search="' + window.document.getElementById('explorer-search').value + '"');
    ok(toasts(window).length === 0, 'B: no warning toast on an idle jump');
    ok(node && node.classList.contains('tree-highlight'), 'B: a pointer leaf under a remembered search stays highlighted');
}

(async function () {
    await caseA();
    await caseB();
    console.log(fails ? fails + ' FAILED' : 'all ok');
    process.exit(fails ? 1 : 0);
})();
