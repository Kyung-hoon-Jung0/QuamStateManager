/* v1.5.1 SA-F10: the Qubits page (and every entity table that renders chip
 * values server-side) follows the working copy.
 *
 * After a Take live the Qubits page still showed q2.T1 = 16.04 us while the
 * pull had brought 44 us and the top bar said "In sync" -- the page has no
 * leaf LiveSurfacePatch can reach. Now on sm:wc-moved (the signal the
 * Live-Edit cells, Chip Status and the CZ plan follow) each
 * table[data-refresh-rows] re-reads its rows only.
 *
 * Pins (the REAL app.js + wc-moved.js under jsdom; htmx stubbed to swap):
 *  A  a tray whose edit seq moved re-reads the table's tbody -- GET of the
 *     current page, target the tbody, select "#qubits-table > tbody",
 *     outerHTML -- and the new values are on screen;
 *  B  the pane, the table and the search box are the same nodes (nothing
 *     swapped), the typed filter and the selected row hold;
 *  C  a table the user is typing into waits until focus leaves it;
 *  D  a table without [data-refresh-rows] is never re-read.
 *
 * Run: node tests/v151_entity_rows_refresh_selfcheck.cjs
 *      (driven by tests/test_v151_entity_rows_refresh.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

function rowsHtml(t1q2) {
    return '<tr class="clickable-row" data-qubit-id="q1" hx-get="/qubit/q1"><td>q1</td><td>16.04</td></tr>'
         + '<tr class="clickable-row" data-qubit-id="q2" hx-get="/qubit/q2"><td>q2</td><td>' + t1q2 + '</td></tr>';
}
const PAGE = '<!doctype html><html><body>'
    + '<div id="topbar-tray-slot"><div id="pending-tray" data-edit-seq="s1"></div></div>'
    + '<div id="table-pane">'
    + '<input id="flt" type="search" oninput="filterTable(this, \'qubits-table\')">'
    + '<small id="qubits-table-filter-count"></small>'
    + '<table id="qubits-table" data-refresh-rows class="data-table"><thead><tr>'
    + '<th class="sortable" data-col="0" data-type="str">ID</th><th class="sortable" data-col="1" data-type="num">T1</th>'
    + '</tr></thead><tbody>' + rowsHtml('16.04') + '</tbody></table>'
    + '</div><div id="inspector-pane"><input id="insp" value="x"></div>'
    + '</body></html>';

const dom = new JSDOM(PAGE, { url: 'http://localhost/qubits?per_page=50', pretendToBeVisual: true,
                              runScripts: 'outside-only' });
const win = dom.window;
const doc = win.document;
let serverT1 = '44.00';
const ajaxCalls = [];
win.htmx = {
    process: function () {}, trigger: function () {},
    ajax: function (verb, url, spec) {
        ajaxCalls.push({ verb: verb, url: url, spec: spec });
        const tb = spec && spec.target;
        if (tb && tb.parentNode && spec.swap === 'outerHTML') {
            const fresh = doc.createElement('tbody');
            fresh.innerHTML = rowsHtml(serverT1);
            tb.parentNode.replaceChild(fresh, tb);
        }
        return Promise.resolve();
    },
};
win.fetch = function () { return new Promise(function () {}); };
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(win, 'localStorage', { value: STORE, configurable: true });
Object.defineProperty(win, 'sessionStorage', { value: STORE, configurable: true });
win.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
win.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
new win.Function(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8') + '\n;\n'
                 + fs.readFileSync(path.join(STATIC, 'wc-moved.js'), 'utf8')).call(win);

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let seq = 1;
function wcMoves() {
    seq++;
    doc.getElementById('pending-tray').setAttribute('data-edit-seq', 's' + seq);
    doc.dispatchEvent(new win.CustomEvent('sm:tray-swapped'));
}
function t1(q) {
    const tr = doc.querySelector('#qubits-table tbody tr[data-qubit-id="' + q + '"]');
    return tr ? tr.cells[1].textContent : null;
}

(async function main() {
    await wait(30);
    const pane = doc.getElementById('table-pane');
    const table = doc.getElementById('qubits-table');
    const search = doc.getElementById('flt');
    // the user filtered to q2 and selected its row
    search.value = 'q2';
    win.filterTable(search, 'qubits-table');
    await wait(200);
    doc.querySelector('#qubits-table tr[data-qubit-id="q2"]').classList.add('row-selected');
    ok(t1('q2') === '16.04', 'setup: the page shows the pre-pull value');

    /* A */
    wcMoves();                                   // a Take live landed
    await wait(400);
    const c = ajaxCalls[0] || {};
    ok(ajaxCalls.length === 1 && c.verb === 'GET' && c.url === '/qubits?per_page=50',
       'A1: the working copy moving re-reads the current page -- ' + JSON.stringify(ajaxCalls.map((x) => x.url)));
    ok(c.spec && c.spec.select === '#qubits-table > tbody' && c.spec.swap === 'outerHTML'
       && c.spec.target && c.spec.target.tagName === 'TBODY',
       'A2: ...its rows only (tbody, outerHTML) -- ' + JSON.stringify(c.spec && { select: c.spec.select, swap: c.spec.swap }));
    ok(t1('q2') === '44.00', 'A3: the new value is on screen -- ' + t1('q2'));

    /* B */
    ok(doc.getElementById('table-pane') === pane && doc.getElementById('qubits-table') === table
       && doc.getElementById('flt') === search && search.value === 'q2',
       'B1: the pane, the table and the search box were not swapped');
    await wait(250);
    const q1 = doc.querySelector('#qubits-table tr[data-qubit-id="q1"]');
    ok(q1 && q1.style.display === 'none', 'B2: the typed filter is applied to the new rows');
    const q2 = doc.querySelector('#qubits-table tr[data-qubit-id="q2"]');
    ok(q2 && q2.classList.contains('row-selected'), 'B3: the selected row stays selected');

    /* C */
    const cell = doc.querySelector('#qubits-table tr[data-qubit-id="q1"] td');
    const inp = doc.createElement('input');
    cell.appendChild(inp);
    inp.focus();
    ok(doc.activeElement === inp, 'C setup: an input inside the table has focus');
    serverT1 = '45.00';
    wcMoves();
    await wait(400);
    ok(ajaxCalls.length === 1, 'C1: nothing is re-read while the user types in the table (' + ajaxCalls.length + ')');
    doc.getElementById('insp').focus();          // focus leaves the table
    await wait(400);
    ok(ajaxCalls.length === 2 && t1('q2') === '45.00',
       'C2: ...and it catches up once focus leaves (' + ajaxCalls.length + ', ' + t1('q2') + ')');

    /* D */
    table.removeAttribute('data-refresh-rows');
    wcMoves();
    await wait(400);
    ok(ajaxCalls.length === 2, 'D1: a table without data-refresh-rows is never re-read (' + ajaxCalls.length + ')');

    if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
    console.log('v151_entity_rows_refresh_selfcheck: all checks passed (' + n + ' assertions)');
    process.exit(0);
})();
