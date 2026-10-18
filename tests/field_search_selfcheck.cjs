/* B1 (UX round) -- the command palette finds actions, and a search result
 * opens the inspector AT its field. The REAL app.js under jsdom:
 *   - Ctrl+K "settings" + Enter opens Settings (an action, not a page load);
 *   - "chip report" + Enter opens the printable report in a new tab;
 *   - a name the palette does not hold ("x180 amplitude") offers the field
 *     search, and Enter runs /search with it;
 *   - focusEditInput(path, {reveal: true}) opens the field's closed section,
 *     marks its row and focuses its input; a field with no row on the
 *     inspector is handed to the inspector's own search box.
 *
 * Run: node tests/field_search_selfcheck.cjs
 * (driven by tests/test_v151_ux_field_search.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const PATH = 'qubits.q1.xy.operations.x180_DragCosine.amplitude';
const data = { qubits: ['q1'], pairs: [], pages: [
    { label: 'Qubits', url: '/qubits' },
    { label: 'CZ gate plan', url: '/cz-plan' },
    { label: 'Settings', action: 'settings', sub: 'theme' },
    { label: 'Chip report (print)', url: '/chip-status/report', action: 'newtab' },
] };
const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<input id="global-search" type="search">' +
    '<div id="cmd-palette" hidden><input id="cmd-palette-input"><ul id="cmd-palette-results"></ul></div>' +
    '<script id="cmd-palette-data" type="application/json">' + JSON.stringify(data) + '</script>' +
    '<div id="table-pane"></div>' +
    '<div id="inspector-pane"><div class="detail-search"><input type="search" class="detail-search-input"></div>' +
    '<article class="qubit-detail"><details class="detail-section"><summary>XY</summary>' +
    '<table class="prop-table"><tbody><tr id="row-x"><td><code class="dot-path" title="' + PATH + ' — click to copy">amplitude</code></td>' +
    '<td><form class="inline-edit"><input type="hidden" name="dot_path" value="' + PATH + '">' +
    '<input type="text" name="value" value="0.3001"></form></td></tr></tbody></table></details></article></div>' +
    '</body></html>',
    { url: 'http://localhost/qubits', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

const ajax = [];
window.fetch = function () { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({}); } }); };
window.htmx = { ajax: function (m, u, o) { ajax.push(u); return Promise.resolve(); },
                on: function () {}, trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
window.trapFocus = function () { return function () {}; };

let settingsOpened = 0;
window.toggleSettings = function () { settingsOpened++; };
const opened = [];
window.open = function (u) { opened.push(u); return null; };

function type(q) {
    const inp = document.getElementById('cmd-palette-input');
    inp.value = q;
    inp.dispatchEvent(new window.Event('input', { bubbles: true }));
}
function key(k) {
    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
}
function labels() {
    return Array.prototype.map.call(document.querySelectorAll('#cmd-palette-results .cmd-palette-label'),
                                    function (e) { return e.textContent; });
}

(async function main() {
    window.openCmdPalette();
    await sleep(20);
    type('settings');
    ok(labels()[0] === 'Settings', 'Settings is offered: ' + JSON.stringify(labels()));
    key('Enter');
    ok(settingsOpened === 1, 'Enter on Settings opens the Settings popover');
    ok(document.getElementById('cmd-palette').hidden, 'the palette closed');

    window.openCmdPalette();
    await sleep(20);
    type('chip report');
    key('Enter');
    ok(opened.length === 1 && opened[0] === '/chip-status/report', 'the chip report opens in a new tab: ' + JSON.stringify(opened));

    window.openCmdPalette();
    await sleep(20);
    type('x180 amplitude');
    const ls = labels();
    ok(ls.length === 1 && /^Search chip fields for "x180 amplitude"/.test(ls[0]),
       'an unknown name offers the field search instead of "No matches": ' + JSON.stringify(ls));
    key('Enter');
    ok(ajax.some(function (u) { return u === '/search?q=x180%20amplitude'; }),
       'Enter runs the field search: ' + JSON.stringify(ajax));
    ok(document.getElementById('global-search').value === 'x180 amplitude', 'the top-bar Search shows the query');

    // reveal: the field's closed section opens, its row is marked, its input focused
    const det = document.querySelector('#inspector-pane details');
    ok(!det.open, 'precondition: the section is closed');
    window.focusEditInput(PATH, { reveal: true });
    await sleep(40);
    ok(det.open, 'the closed section was opened');
    ok(document.getElementById('row-x').classList.contains('field-found'), 'the row is marked');
    ok(document.activeElement && document.activeElement.name === 'value', 'the field input has focus');

    // the plain call (after an edit) never marks
    document.getElementById('row-x').classList.remove('field-found');
    window.focusEditInput(PATH);
    await sleep(40);
    ok(!document.getElementById('row-x').classList.contains('field-found'), 'a plain focus does not mark the row');

    // a field the inspector does not show goes to its own search box
    window.focusEditInput('qubits.q1.xy.operations.x180_DragCosine.sigma', { reveal: true });
    await sleep(40);
    ok(document.querySelector('.detail-search-input').value === 'x180_DragCosine sigma',
       'an absent field is handed to the inspector search: ' + document.querySelector('.detail-search-input').value);

    console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
