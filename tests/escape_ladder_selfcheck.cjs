/* docs/190 section 8 — the Escape ladder and the Pulses URL sync, driven through
 * the REAL app.js under jsdom.
 *
 * Escape has to close the innermost thing the presser is pointing at: a floating
 * topbar tool, then an inline form inside the inspector, then an open slider,
 * then the inspector itself. The first version of the docs/190 F41 fix closed the
 * whole inspector with a Rename box open, which the stress sweep caught.
 *
 * The URL sync has to carry the page number: page 4 of a 156-pulse chip used to
 * reload as page 1.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/pulses', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.history = window.history;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;

function esc(target) {
    const ev = new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    (target || doc.body).dispatchEvent(ev);
    return ev;
}

// ---------------------------------------------------------------- the ladder
function inspectorWithForm(openForm) {
    doc.body.innerHTML =
        '<div id="settings-dropdown" class="settings-hidden"></div>' +
        '<div id="calc-popover" class="calc-hidden"></div>' +
        '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="qubits.q1.xy.operations.x180">' +
        '  <div class="pulse-rename-form"' + (openForm === 'rename' ? '' : ' hidden') + '>' +
        '    <input type="text" name="new_name" value="x180">' +
        '  </div>' +
        '  <div class="pulse-duplicate-form"' + (openForm === 'dup' ? '' : ' hidden') + '>' +
        '    <input type="text" name="new_name" value="x180_copy">' +
        '  </div>' +
        '  <table><tr><td><input class="edit-input" data-param="length" value="40"></td></tr></table>' +
        '</div></div>';
}

let closedInspector = 0;
window.closeInspector = function () { closedInspector++; doc.getElementById('inspector-pane').innerHTML = ''; };

// 1. a rename form open: Escape closes the FORM, keeps the inspector
inspectorWithForm('rename');
closedInspector = 0;
const draft = doc.querySelector('.pulse-rename-form input');
draft.value = 'a_draft_the_user_abandoned';
esc();
ok(doc.querySelector('.pulse-rename-form').hidden === true, 'Escape closed the open Rename form');
ok(closedInspector === 0 && !!doc.getElementById('pulse-detail-root'),
   'Escape with a form open did NOT close the whole inspector');
ok(doc.querySelector('.pulse-rename-form input').value === 'x180',
   'the abandoned draft was reset to the committed name (docs/190 F42)');

// 2. a second Escape now closes the inspector
esc();
ok(closedInspector === 1, 'the next Escape closed the inspector');

// 3. a duplicate form gets the same treatment
inspectorWithForm('dup');
closedInspector = 0;
esc();
ok(doc.querySelector('.pulse-duplicate-form').hidden === true && closedInspector === 0,
   'Escape closed the Duplicate form, not the inspector');

// 4. a floating tool wins over everything below it
inspectorWithForm('rename');
closedInspector = 0;
let toggledSettings = 0;
window.toggleSettings = function () { toggledSettings++; doc.getElementById('settings-dropdown').classList.add('settings-hidden'); };
doc.getElementById('settings-dropdown').classList.remove('settings-hidden');
esc();
ok(toggledSettings === 1, 'Escape closed the open Settings window first');
ok(doc.querySelector('.pulse-rename-form').hidden === false,
   'the form under the open tool was left alone');
ok(closedInspector === 0, 'and the inspector was left alone');

// 5. the calculator too
inspectorWithForm(null);
let toggledCalc = 0;
window.toggleCalc = function () { toggledCalc++; doc.getElementById('calc-popover').classList.add('calc-hidden'); };
doc.getElementById('calc-popover').classList.remove('calc-hidden');
esc();
ok(toggledCalc === 1, 'Escape closed the open Calculator window');

// 6. Escape inside a parameter input still reverts that input, and nothing else
inspectorWithForm(null);
closedInspector = 0;
const cell = doc.querySelector('input.edit-input[data-param]');
cell.value = '99';
esc(cell);
ok(cell.value === '40', 'Escape in an edit cell reverted the cell');
ok(closedInspector === 0, 'and did not close the inspector');

// ------------------------------------------------------- the Pulses URL sync
function pulsesDom(page, perPage, q, channel) {
    doc.body.innerHTML =
        '<div class="table-filter"><input type="search" name="q" value="' + (q || '') + '"></div>' +
        '<nav id="pulse-channel-tabs"><ul>' +
        '<li><a hx-get="/pulses?rows=1&per_page=50" class="' + (channel ? '' : 'active') + '">All</a></li>' +
        '<li><a hx-get="/pulses?rows=1&channel=xy&per_page=50" class="' + (channel === 'xy' ? 'active' : '') + '">XY</a></li>' +
        '</ul></nav>' +
        '<input type="hidden" id="pulses-owner-pick" value="">' +
        '<div id="pulses-rows-wrap"><span class="page-info" data-current-page="' + page + '">Page ' + page + '</span>' +
        '<select name="per_page"><option value="25">25</option><option value="50" selected>50</option>' +
        '<option value="100">100</option><option value="0">All</option></select></div>';
    if (perPage) doc.querySelector('select[name=per_page]').value = String(perPage);
}

pulsesDom(2, 50, '', '');
window._pulsesSyncUrl();
ok(window.location.search.indexOf('page=2') >= 0,
   'the URL carries the page number (got ' + window.location.search + ')');

pulsesDom(1, 50, 'x180', 'xy');
window._pulsesSyncUrl();
ok(window.location.search.indexOf('page=') < 0,
   'page 1 is not written into the URL (got ' + window.location.search + ')');
ok(window.location.search.indexOf('channel=xy') >= 0 && window.location.search.indexOf('q=x180') >= 0,
   'the channel and the query are still written');

pulsesDom(3, 100, '', '');
window._pulsesSyncUrl();
ok(window.location.search.indexOf('per_page=100') >= 0 && window.location.search.indexOf('page=3') >= 0,
   'a non-default rows-per-page is carried too (got ' + window.location.search + ')');

// and the sync is wired to the swap that CHANGES the page (the pagination links
// target #table-pane, which is why the first attempt at this fix did nothing)
pulsesDom(4, 50, '', '');
window.history.replaceState({}, '', '/pulses');
const pane = doc.createElement('div'); pane.id = 'table-pane'; doc.body.appendChild(pane);
const ev = new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: {} });
Object.defineProperty(ev, 'target', { value: pane });
doc.dispatchEvent(ev);
ok(window.location.search.indexOf('page=4') >= 0,
   'a #table-pane swap re-syncs the URL (got ' + window.location.search + ')');

// ------------------------------------------- F21: the row click wins the race
// A blur-commit is issued first and its response re-renders the OLD pulse, so
// it can land after the row's own /pulse/detail and overwrite it. The row is
// what the user pointed at: it wins, deterministically.
{
    doc.body.innerHTML =
        '<table id="pulses-table"><tbody>' +
        '<tr class="clickable-row" data-pulse-path="qubits.q1.xy.operations.saturation"><td>q1</td></tr>' +
        '<tr class="clickable-row" data-pulse-path="qubits.q2.xy.operations.saturation"><td>q2</td></tr>' +
        '</tbody></table>' +
        '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="qubits.q1.xy.operations.saturation">' +
        '<form class="inline-edit"><input name="value" data-param="length" data-committed="600" value="1299"></form>' +
        '</div></div>';
    const calls = [];
    window.htmx.ajax = (verb, path, opts) => { calls.push(path); return Promise.resolve(); };

    // the user clicks the second row while the field holds uncommitted text
    doc.querySelectorAll('tr.clickable-row')[1].dispatchEvent(
        new window.MouseEvent('click', { bubbles: true }));
    ok(window._pulseNavWanted === 'qubits.q2.xy.operations.saturation',
       'the clicked row is remembered while an edit is uncommitted');

    function swap(fromPath, renderedPath) {
        doc.getElementById('inspector-pane').innerHTML =
            '<div id="pulse-detail-root" data-pulse-path="' + renderedPath + '"></div>';
        const ev = new window.CustomEvent('htmx:afterSwap', {
            bubbles: true, detail: { pathInfo: { requestPath: fromPath } } });
        Object.defineProperty(ev, 'target', { value: doc.getElementById('inspector-pane') });
        doc.dispatchEvent(ev);
    }
    // the row's own response lands first and is correct: nothing re-issued
    swap('/pulse/detail?path=qubits.q2.xy.operations.saturation', 'qubits.q2.xy.operations.saturation');
    ok(calls.length === 0, 'the row response alone re-issues nothing');
    // ...then the commit's response overwrites it with the OLD pulse
    swap('/pulse/edit', 'qubits.q1.xy.operations.saturation');
    ok(calls.length === 1 && calls[0].indexOf('qubits.q2.xy.operations.saturation') > 0,
       'the commit response that clobbered the view re-issues the clicked row (got '
       + JSON.stringify(calls) + ')');
    ok(window._pulseNavWanted === null, 'and the intent is consumed');

    // a click with NO uncommitted edit remembers nothing
    doc.body.innerHTML = doc.body.innerHTML.replace('value="1299"', 'value="600"');
    doc.querySelectorAll('tr.clickable-row')[0].dispatchEvent(
        new window.MouseEvent('click', { bubbles: true }));
    ok(!window._pulseNavWanted, 'a click with a clean field remembers nothing');
}

// --------------------------------------------- F36: a sort survives a refetch
{
    doc.body.innerHTML =
        '<table id="pulses-table"><thead><tr>' +
        '<th class="sortable" data-col="0" data-type="str">OWNER</th>' +
        '<th class="sortable" data-col="1" data-type="num">LENGTH</th>' +
        '</tr></thead><tbody>' +
        '<tr><td>q3</td><td>300</td></tr><tr><td>q1</td><td>100</td></tr><tr><td>q2</td><td>200</td></tr>' +
        '</tbody></table>';
    const th = doc.querySelectorAll('th.sortable')[1];
    th.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    const order1 = Array.from(doc.querySelectorAll('#pulses-table tbody tr')).map(r => r.cells[1].textContent).join(',');
    ok(order1 === '100,200,300', 'a click sorts ascending (got ' + order1 + ')');
    const table = doc.getElementById('pulses-table');
    ok(table.getAttribute('data-sorted-col') === '1' && table.getAttribute('data-sorted-dir') === 'asc',
       'the table remembers which column it is sorted by');

    // the refetch replaces the rows in the ORIGINAL order
    doc.querySelector('#pulses-table tbody').innerHTML =
        '<tr><td>q3</td><td>300</td></tr><tr><td>q1</td><td>100</td></tr><tr><td>q2</td><td>200</td></tr>';
    const ev = new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: {} });
    Object.defineProperty(ev, 'target', { value: doc.getElementById('pulses-table') });
    doc.dispatchEvent(ev);
    const order2 = Array.from(doc.querySelectorAll('#pulses-table tbody tr')).map(r => r.cells[1].textContent).join(',');
    ok(order2 === '100,200,300', 'the sort is re-applied after the swap (got ' + order2 + ')');
}

process.exit(fails ? 1 : 0);
