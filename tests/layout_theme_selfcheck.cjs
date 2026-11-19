/* Audit 2026-11-13 (layout / theme / keyboard) in the REAL app.js + the REAL
 * theme boot under jsdom:
 *  - F13: below the narrow breakpoint the sidebar collapses on its own and
 *    opens as an overlay; the user's stored wide-window choice is never
 *    written by that, and is restored when the window widens again.
 *  - the OS colour scheme decides the theme only while nothing is stored;
 *    a live change of it re-resolves; a stored choice always wins.
 *
 * Run: node tests/layout_theme_selfcheck.cjs  (driven by tests/test_layout_theme.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const WEB = path.join(__dirname, '..', 'quam_state_manager', 'web');
const read = (n) => fs.readFileSync(path.join(WEB, n), 'utf8');
const BOOT = read('templates/_theme_boot.html').match(/<script>([\s\S]*?)<\/script>/)[1];
const APP = read('static/app.js');

let fails = 0;
const pending = [];
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

/* A controllable matchMedia: each query has a `matches` the test sets and
   change listeners the test fires. */
function mediaWorld(win, state) {
    const lists = {};
    win.matchMedia = function (q) {
        if (!lists[q]) {
            lists[q] = { media: q, listeners: [],
                get matches() { return !!state[q]; },
                addEventListener(t, f) { if (t === 'change') this.listeners.push(f); },
                removeEventListener() {} };
        }
        return lists[q];
    };
    return function set(q, v) {
        state[q] = v;
        const l = lists[q];
        if (l) l.listeners.forEach((f) => f({ matches: v, media: q }));
    };
}

function makeWorld(opts) {
    const dom = new JSDOM('<!doctype html><html><head></head><body>'
        + '<header class="topbar"><button class="sidebar-toggle" onclick="cycleChrome()">=</button></header>'
        + '<div class="app-layout"><aside id="sidebar"><nav class="sidebar-nav">'
        + '<a href="/bulk" id="nav-link">Live edit</a></nav><input id="sb-search"></aside>'
        + '<main id="main"><div id="table-pane"><p id="page">page</p></div></main></div>'
        + '</body></html>', { url: 'http://localhost/' + (opts.qs || ''), pretendToBeVisual: true,
        runScripts: 'outside-only' });
    const w = dom.window;
    const store = Object.assign({}, opts.store || {});
    const ls = { getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
    Object.defineProperty(w, 'localStorage', { value: ls, configurable: true });
    const set = mediaWorld(w, Object.assign({}, opts.media || {}));
    w.fetch = () => new Promise(() => {});
    w.requestAnimationFrame = (f) => setTimeout(f, 0);
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
    w.eval(BOOT);
    w.eval(APP);
    w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
    // runScripts 'outside-only' ignores onclick attributes: wire the bar button
    w.document.querySelector('.sidebar-toggle').addEventListener('click', () => w.cycleChrome());
    return { w, doc: w.document, store, set };
}

const NARROW = '(max-width: 767px)';
const LIGHT = '(prefers-color-scheme: light)';

/* ---- F13: narrow window ---------------------------------------------- */
{
    const { w, doc, store, set } = makeWorld({ store: { quam_sidebar_collapsed: '0' }, media: { [NARROW]: true } });
    const L = doc.querySelector('.app-layout');
    ok(L.classList.contains('sidebar-narrow') && L.classList.contains('sidebar-collapsed'),
       'a narrow window starts with the sidebar collapsed (narrow mode)');
    ok(store.quam_sidebar_collapsed === '0', 'the auto-collapse never writes the stored choice');
    doc.querySelector('.sidebar-toggle').click();
    ok(!L.classList.contains('sidebar-collapsed'), 'the bar button opens the overlay');
    ok(!doc.documentElement.classList.contains('topbar-hidden'), 'and never hides the top bar in narrow mode');
    ok(store.quam_sidebar_collapsed === '0', 'opening the overlay is not stored');
    doc.getElementById('sb-search').click();
    ok(!L.classList.contains('sidebar-collapsed'), 'a click inside the sidebar keeps it open');
    doc.getElementById('page').click();
    ok(L.classList.contains('sidebar-collapsed'), 'a click on the page closes the overlay');
    w.toggleSidebar();
    doc.getElementById('nav-link').addEventListener('click', (e) => e.preventDefault());
    doc.getElementById('nav-link').click();
    ok(L.classList.contains('sidebar-collapsed'), 'a nav link inside the overlay closes it');
    w.toggleSidebar();
    doc.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    ok(L.classList.contains('sidebar-collapsed'), 'Escape closes the overlay');
    ok(store.quam_sidebar_collapsed === '0', 'nothing in narrow mode was stored');
    set(NARROW, false);
    ok(!L.classList.contains('sidebar-narrow') && !L.classList.contains('sidebar-collapsed'),
       'widening restores the stored choice (open)');
    w.toggleSidebar();
    ok(store.quam_sidebar_collapsed === '1', 'a wide-window toggle is stored as before');
    set(NARROW, true);
    ok(L.classList.contains('sidebar-collapsed'), 'narrowing again collapses');
    w.toggleSidebar();
    set(NARROW, false);
    ok(L.classList.contains('sidebar-collapsed') && store.quam_sidebar_collapsed === '1',
       'widening restores a stored collapsed choice even after the overlay was opened');
}
{
    const { doc, store } = makeWorld({ store: { quam_sidebar_collapsed: '0' }, media: { [NARROW]: false } });
    const L = doc.querySelector('.app-layout');
    ok(!L.classList.contains('sidebar-narrow') && !L.classList.contains('sidebar-collapsed'),
       'a wide window is untouched');
    doc.getElementById('page').click();
    ok(!L.classList.contains('sidebar-collapsed') && store.quam_sidebar_collapsed === '0',
       'a click on the page never collapses a wide sidebar');
}

/* ---- Back/Forward with no cached page: htmx swaps in a fresh body whose
   .app-layout carries neither class; the narrow rule must apply to it again,
   or the page is squeezed beside an open 300 px sidebar. ------------------- */
{
    const { w, doc } = makeWorld({ store: { quam_sidebar_collapsed: '0' }, media: { [NARROW]: true } });
    const old = doc.querySelector('.app-layout');
    const fresh = doc.createElement('div');
    fresh.className = 'app-layout';
    fresh.innerHTML = '<aside id="sidebar"></aside><main id="main"></main>';
    old.replaceWith(fresh);
    doc.body.dispatchEvent(new w.CustomEvent('htmx:historyRestore', { bubbles: true, detail: { path: '/x' } }));
    ok(fresh.classList.contains('sidebar-narrow') && fresh.classList.contains('sidebar-collapsed'),
       'a history restore at a narrow width collapses the new layout again');
    ok(doc.getElementById('sidebar').hasAttribute('inert'),
       'and the restored sidebar takes no Tab stop while collapsed');
    w.toggleSidebar();
    // The class watch is a MutationObserver: it answers after the current task.
    pending.push(new Promise((done) => setTimeout(() => {
        ok(!doc.getElementById('sidebar').hasAttribute('inert'),
           'opening the restored sidebar makes it reachable again (the class watch follows the new layout)');
        done();
    }, 0)));
}
{
    const { w, doc } = makeWorld({ store: { quam_sidebar_collapsed: '1' }, media: { [NARROW]: false } });
    const fresh = doc.createElement('div');
    fresh.className = 'app-layout';
    fresh.innerHTML = '<aside id="sidebar"></aside>';
    doc.querySelector('.app-layout').replaceWith(fresh);
    doc.body.dispatchEvent(new w.CustomEvent('htmx:historyRestore', { bubbles: true, detail: { path: '/x' } }));
    ok(!fresh.classList.contains('sidebar-narrow') && fresh.classList.contains('sidebar-collapsed'),
       'a history restore at a wide width keeps the stored collapsed choice');
}

/* ---- theme ------------------------------------------------------------ */
{
    const { w, doc, store, set } = makeWorld({ media: { [LIGHT]: true } });
    const H = doc.documentElement;
    ok(H.getAttribute('data-theme') === 'light', 'no stored theme + OS light opens light');
    set(LIGHT, false);
    ok(H.getAttribute('data-theme') === 'qm', 'an OS switch to dark follows live (QM default)');
    ok(!('quam_theme' in store), 'following the OS stores nothing');
    set(LIGHT, true);
    ok(H.getAttribute('data-theme') === 'light', 'and back to light');
    w.setTheme('dark');
    ok(store.quam_theme === 'dark', 'an explicit choice is stored');
    set(LIGHT, false); set(LIGHT, true);
    ok(H.getAttribute('data-theme') === 'dark', 'after a choice the OS no longer decides');
}
{
    const { doc } = makeWorld({ store: { quam_theme: 'qm' }, media: { [LIGHT]: true } });
    ok(doc.documentElement.getAttribute('data-theme') === 'qm', 'a stored choice wins over the OS at boot');
}
{
    const { doc } = makeWorld({ media: { [LIGHT]: false } });
    ok(doc.documentElement.getAttribute('data-theme') === 'qm', 'no stored theme + OS dark keeps the QM default');
}
{
    const { doc, set } = makeWorld({ qs: '?theme=dark', media: { [LIGHT]: true } });
    ok(doc.documentElement.getAttribute('data-theme') === 'dark', 'a ?theme= override wins over the OS');
    set(LIGHT, false); set(LIGHT, true);
    ok(doc.documentElement.getAttribute('data-theme') === 'dark', 'and an OS change does not move it');
}

Promise.all(pending).then(() => process.exit(fails ? 1 : 0));
