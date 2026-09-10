/* A command-palette pick is a NAVIGATION: the address bar follows the pane,
 * and only when the pane actually moved.
 *
 * The line this pins was wrong twice. It first passed `pushUrl` to
 * htmx.ajax -- not an option htmx 2 has, so the pane changed and the address
 * bar did not, and Back left the app. The replacement pushed from the ajax
 * PROMISE, which four reviewers then measured settles the same way whether a
 * swap happened or not: htmx resolves it on a 404, resolves it immediately
 * when Bundles' htmx:confirm handler cancels the request to wait for a page
 * bundle, and `_push` was the rejection handler too. So a failed pick moved
 * the address bar to a page that never loaded, permanently.
 *
 * The swap is the event that means "the pane now shows this URL". Everything
 * below drives the REAL app.js through a real palette click.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const APP = fs.readFileSync(
    path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let checks = 0, fails = 0;
function ok(cond, msg) {
    checks++;
    if (!cond) { fails++; console.log('FAIL: ' + msg); }
}

function world() {
    const dom = new JSDOM(
        '<!DOCTYPE html><html><body>'
        + '<div id="cmd-palette" hidden><input id="cmd-palette-input">'
        + '<ul id="cmd-palette-results"></ul></div>'
        + '<div id="table-pane"></div><div id="inspector-pane"></div>'
        + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true,
          url: 'http://localhost/diagnostics' });
    const win = dom.window;
    win._pushes = [];
    win._navSyncs = 0;
    win._ajax = [];
    win.htmx = { ajax: function (verb, url, ctx) { win._ajax.push([verb, url, ctx]); } };
    win.fetch = function () { return new win.Promise(function () {}); };
    win.eval(APP);
    // after app.js, so the real one is replaced rather than shadowed
    win.eval('window.syncSidebarNavActive = function () { window._navSyncs++; };');
    const realPush = win.history.pushState.bind(win.history);
    win.history.pushState = function (st, t, u) {
        win._pushes.push(u);
        try { realPush(st, t, u); } catch (e) { /* jsdom cross-origin */ }
    };
    return win;
}

// The palette's own click path: seed a recent, open, click the row.
function pick(win, url) {
    win.localStorage.setItem('cmd_palette_recents', JSON.stringify(
        [{ type: 'page', label: 'a page', sub: '', url: url }]));
    win.openCmdPalette();
    const li = win.document.querySelector('#cmd-palette-results .cmd-palette-item');
    if (!li) return false;
    li.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    return true;
}

function swap(win, url, okFlag) {
    const pane = win.document.getElementById('table-pane');
    const detail = { pathInfo: { requestPath: url, finalRequestPath: url },
                     successful: okFlag !== false };
    if (okFlag !== false) {
        pane.dispatchEvent(new win.CustomEvent('htmx:afterSwap',
            { bubbles: true, detail: detail }));
    }
    pane.dispatchEvent(new win.CustomEvent('htmx:afterRequest',
        { bubbles: true, detail: detail }));
}

/* 1. the ordinary pick */
(function () {
    const win = world();
    ok(pick(win, '/param-history'), '1a the palette rendered a clickable row');
    ok(win._ajax.length === 1 && win._ajax[0][1] === '/param-history',
       '1b one request, for the picked url');
    ok(win._ajax[0][2] && win._ajax[0][2].source === '#table-pane',
       '1c it names a source element (hx-sync queues on the pane)');
    ok(!('pushUrl' in win._ajax[0][2]) && !('pushURL' in win._ajax[0][2]),
       '1d and passes no dead pushUrl option');
    ok(win._pushes.length === 0, '1e nothing is pushed BEFORE the swap');
    swap(win, '/param-history');
    ok(win._pushes.length === 1 && win._pushes[0] === '/param-history',
       '1f the swap pushes the entry');
    ok(win._navSyncs >= 1,
       '1g ...and re-syncs the sidebar (a raw pushState fires no htmx event)');
})();

/* 2. a pick that FAILS must not move the address bar */
(function () {
    const win = world();
    pick(win, '/dataset/deadbeef-not-a-run');
    swap(win, '/dataset/deadbeef-not-a-run', false);   // afterRequest, no swap
    ok(win._pushes.length === 0,
       '2a a request that never swapped pushes nothing');
    // ...and the listener is gone, so an unrelated later swap cannot push it
    swap(win, '/dataset/deadbeef-not-a-run', true);
    ok(win._pushes.length === 0,
       '2b the failed pick left no listener behind to fire later');
})();

/* 3. a swap for a DIFFERENT url is not this pick's */
(function () {
    const win = world();
    pick(win, '/param-history');
    swap(win, '/diagnostics');
    ok(win._pushes.length === 0, '3a another pane load does not push our url');
    swap(win, '/param-history');
    ok(win._pushes.length === 1 && win._pushes[0] === '/param-history',
       '3b ours still pushes when it lands');
})();

/* 4. PaneState's skip path already pushed: never a second entry for one url */
(function () {
    const win = world();
    pick(win, '/bulk');
    // the skip handler pushes synchronously, before any swap
    win.history.pushState({ htmx: true }, '', '/bulk');
    ok(win._pushes.length === 1, '4a (the skip push itself)');
    swap(win, '/bulk');
    ok(win._pushes.length === 1,
       '4b the pick does not add a SECOND entry for the same address');
    ok(win._navSyncs >= 1, '4c the sidebar is still re-synced');
})();

/* 5. one swap, one entry, however many events arrive */
(function () {
    const win = world();
    pick(win, '/param-history');
    swap(win, '/param-history');
    swap(win, '/param-history');
    ok(win._pushes.length === 1, '5a a second swap does not push again');
    /* Two independent mechanisms hold this, and it is worth saying which:
       _onSwap removes both listeners BEFORE it pushes, AND _push refuses an
       address that is already current. Removing the first alone leaks a
       listener but changes nothing observable -- measured, a GREEN mutation
       that is a genuine no-op rather than a gap in this pin. */
})();

/* 6. an entity pick still goes to the inspector and pushes nothing */
(function () {
    const win = world();
    win.localStorage.setItem('cmd_palette_recents', JSON.stringify(
        [{ type: 'qubit', label: 'q1', sub: '', url: '/qubit/q1' }]));
    win.openCmdPalette();
    const li = win.document.querySelector('#cmd-palette-results .cmd-palette-item');
    if (li) li.dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(win._ajax.length === 1 && win._ajax[0][2].target === '#inspector-pane',
       '6a a qubit pick loads the inspector');
    swap(win, '/qubit/q1');
    ok(win._pushes.length === 0,
       '6b an inspector load is not a navigation and pushes nothing');
})();

if (fails === 0) console.log('all checks passed (' + checks + ' assertions)');
process.exit(fails ? 1 : 0);
