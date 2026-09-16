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
        '<li><a hx-get="/pulses?rows=1&channel=z&per_page=50" class="' + (channel === 'z' ? 'active' : '') + '">Z</a></li>' +
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

// ---------------------- F37: a channel tab is a place you can come back from
// Every sync used replaceState, so two tab presses left ONE history entry and
// Back left the page. A pushed entry with no listener would be worse than
// none, so the restore is pinned beside the push.
pulsesDom(1, 50, '', '');
window.history.replaceState({}, '', '/pulses');
const tabZ = doc.querySelector('#pulse-channel-tabs a[hx-get*="channel=xy"]');
const lenBefore = window.history.length;
window.pulseTabActive(tabZ);
ok(window.location.search.indexOf('channel=xy') >= 0,
   'the tab is written to the URL (got ' + window.location.search + ')');
ok(window.history.length > lenBefore,
   'and a channel tab PUSHES (history ' + lenBefore + ' -> ' + window.history.length + ')');

// typing is not a destination: it must still replace
const lenAfterTab = window.history.length;
doc.querySelector('.table-filter input[name="q"]').value = 'x180';
window._pulsesSyncUrl();
ok(window.history.length === lenAfterTab,
   'typing still replaces (history ' + lenAfterTab + ' -> ' + window.history.length + ')');

// pressing the SAME tab twice is one destination, not two
const lenSame = window.history.length;
window.pulseTabActive(tabZ);
ok(window.history.length === lenSame,
   'the same tab again pushes nothing (history ' + lenSame + ' -> ' + window.history.length + ')');

// and a popstate restores the controls AND re-fetches for that exact query
let restored = [];
window.htmx.ajax = function (m, url) { restored.push(url); return Promise.resolve(); };
window.history.replaceState({}, '', '/pulses?channel=z&q=sat&page=3');
window._pulsesRestoreFromUrl();
ok(doc.querySelector('.table-filter input[name="q"]').value === 'sat',
   'Back restores the search box');
const act = doc.querySelector('#pulse-channel-tabs a.active');
ok(act && /channel=z/.test(act.getAttribute('hx-get') || ''),
   'Back restores the active tab');
ok(restored.length === 1 && /rows=1/.test(restored[0])
   && /channel=z/.test(restored[0]) && /q=sat/.test(restored[0])
   && /page=3/.test(restored[0]),
   'and re-fetches the rows for that exact query (got ' + restored[0] + ')');

// off the Pulses page it must do nothing at all
restored = [];
window.history.replaceState({}, '', '/bulk');
window._pulsesRestoreFromUrl();
ok(restored.length === 0, 'a popstate elsewhere fetches nothing');
window.history.replaceState({}, '', '/pulses');

// -------------------------------- F39: the URL carries the OPEN pulse
// The address named the search, the channel, the owner pick and the page --
// everything except the pulse a person was actually looking at, so a reload,
// a Back, or a link to a colleague came back to an empty inspector.
function inspectorWith(pulsePath) {
    let insp = doc.getElementById('inspector-pane');
    if (!insp) { insp = doc.createElement('div'); insp.id = 'inspector-pane'; doc.body.appendChild(insp); }
    insp.innerHTML = pulsePath
        ? '<div id="pulse-detail-root" data-pulse-path="' + pulsePath + '"></div>'
        : '<div id="pulse-create-root"></div>';   // the create form carries none
    return insp;
}

pulsesDom(1, 50, '', '');
inspectorWith('qubits.q1.xy.operations.x180_DragCosine');
window.history.replaceState({}, '', '/pulses');
window._pulsesSyncUrl();
ok(window.location.search.indexOf('pulse=qubits.q1.xy.operations.x180_DragCosine') >= 0,
   'the URL carries the open pulse (got ' + window.location.search + ')');

// the create form is not a pulse: the parameter must be DROPPED, not left
// pinned to whatever was open before it
pulsesDom(1, 50, '', '');
inspectorWith(null);
window._pulsesSyncUrl();
ok(window.location.search.indexOf('pulse=') < 0,
   'the create form drops it (got ' + window.location.search + ')');

// and opening one is what re-syncs: the swap lands in #inspector-pane, which
// is not the rows wrap and not the table pane
pulsesDom(1, 50, '', '');
const insp2 = inspectorWith('qubits.q2.z.operations.const');
window.history.replaceState({}, '', '/pulses');
const ev39 = new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: {} });
Object.defineProperty(ev39, 'target', { value: insp2 });
doc.dispatchEvent(ev39);
ok(window.location.search.indexOf('pulse=qubits.q2.z.operations.const') >= 0,
   'an inspector swap re-syncs the URL (got ' + window.location.search + ')');

// a swap on another page must not rewrite that page's address
window.history.replaceState({}, '', '/bulk');
const ev39b = new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: {} });
Object.defineProperty(ev39b, 'target', { value: insp2 });
doc.dispatchEvent(ev39b);
ok(window.location.pathname === '/bulk',
   'a swap off the Pulses page leaves the address alone (got ' + window.location.pathname + ')');
window.history.replaceState({}, '', '/pulses');
inspectorWith(null);
doc.getElementById('inspector-pane').remove();

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

// ------------------------- F15: the commit must not eat a keystroke
// The focus restore used to hang on htmx's afterSETTLE, a tick after the
// content lands, so for ~20-120ms after every commit focus was on <body>: a
// Ctrl+A typed there went to the document and the Backspace after it ate a
// digit of the committed value (530 + "select all, 540" wrote 53540).
{
    doc.body.innerHTML =
        '<div id="inspector-pane">' +
        '<form class="inline-edit"><input type="hidden" name="dot_path" value="q.x.length">' +
        '<input name="value" class="edit-input" data-param="length" data-committed="600" value="600"></form>' +
        '</div>';
    const input = doc.querySelector('input[name=value]');
    input.focus();
    window.InlineCommit.remember(input, input);
    ok(!!window.InlineCommit._pending(), 'the commit remembered the field');

    // the swap: the pane is replaced and focus is lost to <body>
    doc.getElementById('inspector-pane').innerHTML =
        '<form class="inline-edit"><input type="hidden" name="dot_path" value="q.x.length">' +
        '<input name="value" class="edit-input" data-param="length" data-committed="530" value="530"></form>';
    doc.body.focus();
    const swap = new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: {} });
    Object.defineProperty(swap, 'target', { value: doc.getElementById('inspector-pane') });
    doc.dispatchEvent(swap);
    const active = doc.activeElement;
    ok(active && active.getAttribute && active.getAttribute('data-param') === 'length',
       'focus is back in the field AT THE SWAP, not a settle later (got '
       + (active ? active.tagName + ':' + active.getAttribute('data-param') : 'none') + ')');
}

// ...and a keystroke that still lands in the hole is buffered, not half-applied
{
    window.InlineCommit._clearBuffer();
    doc.body.innerHTML =
        '<div id="inspector-pane">' +
        '<form class="inline-edit"><input type="hidden" name="dot_path" value="q.x.length">' +
        '<input name="value" class="edit-input" data-param="length" data-committed="600" value="600"></form>' +
        '</div>';
    const inp = doc.querySelector('input[name=value]');
    inp.focus();
    window.InlineCommit.remember(inp, inp);
    doc.getElementById('inspector-pane').innerHTML = '';          // the node is doomed
    // the user keeps typing while focus sits on <body>
    ['a', 'Backspace', '5', '4', '0'].forEach(function (k) {
        const ev = new window.KeyboardEvent('keydown',
            { key: k, ctrlKey: k === 'a', bubbles: true, cancelable: true });
        doc.body.dispatchEvent(ev);
    });
    ok(window.InlineCommit._bufferLen() === 5, 'the keystrokes were buffered (got '
       + window.InlineCommit._bufferLen() + ')');
    const fresh = doc.createElement('input');
    fresh.value = '530';
    const carried = window.InlineCommit._applyBuffer(fresh);
    ok(carried && fresh.value === '540',
       'the buffer replays as SELECT-ALL then 540, never 53540 (got ' + fresh.value + ')');
    window.InlineCommit._clearBuffer();
}

// ------------------- F22: a refused revert always SAYS something
// The tray's Revert targets #status-bar. htmx drops 4xx bodies, and only 409
// was allowed through, so a forced revert whose snapshot had been pruned (404,
// with the reason in the body) landed the user an EMPTY status bar.
{
    doc.body.innerHTML = '<div id="status-bar"></div>';
    function beforeSwap(status, path) {
        // the handler reads evt.detail.TARGET (htmx's swap target), not the
        // event target -- a fixture that only sets the latter tests nothing
        const ev = new window.CustomEvent('htmx:beforeSwap', {
            bubbles: true, cancelable: true,
            detail: { shouldSwap: false, isError: true,
                      target: doc.getElementById('status-bar'),
                      xhr: { status: status, responseText: 'the reason' },
                      requestConfig: { path: path } },
        });
        Object.defineProperty(ev, 'target', { value: doc.getElementById('status-bar') });
        doc.dispatchEvent(ev);
        return ev.detail;
    }
    ok(beforeSwap(409, '/state-history/2026-01-01/stage?from=tray').shouldSwap === true,
       'the 409 confirm still renders in the status bar');
    ok(beforeSwap(404, '/state-history/2026-01-01/stage?force=1&from=tray').shouldSwap === true,
       'a 404 from the same door renders its reason too');
    ok(beforeSwap(404, '/something/else').shouldSwap === false,
       'and nothing else is let through');
}

// ---------------------- F05: a passive window follows a foreign edit
// ...but never takes the pane away from someone using it.
{
    doc.body.innerHTML =
        '<div id="pending-tray" data-change-count="0" data-change-sig="aaa"></div>' +
        '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="q.x.sat">' +
        '<form class="inline-edit"><input name="value" data-param="length" value="600"></form>' +
        '</div></div>';
    const calls = [];
    window.htmx.ajax = (verb, path) => { calls.push(path); return Promise.resolve(); };
    window.__lastUserAct = 0;                       // nobody has touched this window
    // drive the POLL's own decision, not a copy of it
    window._editSeqSeen = undefined;
    ok(window._onDriftEditSeq({ edit_seq: "sig-a:0" }) === false,
       'the first payload only records where the chip is');
    ok(calls.length === 0, 'and refreshes nothing');
    ok(window._onDriftEditSeq({ edit_seq: "sig-a:0" }) === false,
       'an unchanged payload is not a signal');
    ok(window._onDriftEditSeq({ edit_seq: "sig-b:1" }) === true,
       'a changed change-set IS a signal');
    ok(calls.some(p => p.indexOf('/state/tray') === 0), 'the tray is refreshed (got ' + JSON.stringify(calls) + ')');
    ok(calls.some(p => p.indexOf('/pulse/detail') === 0), 'an idle window re-reads the open pulse');

    // an in-flight refresh coalesces the next signal (one request, not two)
    calls.length = 0;
    window._refreshAfterForeignEdit();
    ok(calls.length === 0, 'a second signal during the refresh coalesces');

    setTimeout(function () {
        // the same window one keystroke later: the tray still refreshes, the
        // pane does not
        calls.length = 0;
        window.__lastUserAct = Date.now();
        window._refreshAfterForeignEdit();
        ok(calls.some(p => p.indexOf('/state/tray') === 0),
           'a busy window still gets the truthful tray (got ' + JSON.stringify(calls) + ')');
        ok(!calls.some(p => p.indexOf('/pulse/detail') === 0),
           'a window with a user in it keeps its pane');

        setTimeout(function () {
            // and never while the focus is inside the inspector
            calls.length = 0;
            window.__lastUserAct = 0;
            doc.querySelector('input[data-param]').focus();
            window._refreshAfterForeignEdit();
            ok(!calls.some(p => p.indexOf('/pulse/detail') === 0),
               'focus inside the inspector keeps the pane');
            process.exit(fails ? 1 : 0);
        }, 30);
    }, 30);
}
