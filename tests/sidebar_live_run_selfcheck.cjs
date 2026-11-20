/* The sidebar tree and new runs.
 * (1) A run landing (`sm:runs-changed`, the same wake the Datasets table polls
 *     on) makes the tree check its version at once, not on its next interval;
 *     a wake during an in-flight check is not lost.
 * (2) A tree refetch rebuilds every entry with no active marker: the marker
 *     goes back on the run the detail pane shows, nothing else is opened or
 *     scrolled, and no other row is marked.
 * Runs the real sources (the base.html sidebar script, app.js) under jsdom.
 * Driven by tests/test_datasets_live_updates.py. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const WEB = path.join(__dirname, '..', 'quam_state_manager', 'web');
const strip = (s) => s.split(String.fromCharCode(13)).join('');
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function sidebarScript() {
    const html = strip(fs.readFileSync(path.join(WEB, 'templates', 'base.html'), 'utf8'));
    const a = html.indexOf('/* Sidebar tree refresh');
    assert.ok(a > 0, 'the sidebar refresh script is in base.html');
    const open = html.lastIndexOf('<script>', a);
    const end = html.indexOf('</script>', a);
    return html.slice(open + '<script>'.length, end);
}

async function wakeChecks() {
    const dom = new JSDOM('<!doctype html><body><div id="sidebar-tree"></div></body>',
                          { runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    const polls = [];
    let hold = null;
    w.fetch = function (url) {
        polls.push(String(url));
        const body = { v: 1, rescanned: true };
        const res = { json: () => Promise.resolve(body) };
        if (hold) return new Promise((r) => { hold.push(() => r(res)); });
        return Promise.resolve(res);
    };
    w.document.getElementById('sidebar-tree').innerHTML = '<details class="tree-dir" open '
        + 'data-tpath="2026-11-20"><summary>day</summary><ul class="tree-entries">'
        + '<li class="tree-entry-click">run</li></ul></details>';
    const refreshes = [];
    w.htmx = { ajax: (method, url, opts) => {
        refreshes.push({ method, url, opts });
        return Promise.resolve();
    }, process: () => {} };
    w.UI_CONFIG = { autoRefreshInterval: 3600 };   // the interval never fires in this check
    const scope = {
        alive: true,
        cleanup() {}, ready(fn) { fn(); },
        listen(el, ev, fn) { el.addEventListener(ev, fn); },
        timeout(fn, ms) { return setTimeout(fn, ms); },
        clearTimeout(id) { clearTimeout(id); },
        interval() { return 0; },
    };
    w.__smBase = { replace: () => scope };
    w.eval(sidebarScript());
    await wait(30);
    assert.equal(refreshes.length, 1, 'baseline refresh includes the visible group');
    assert.deepEqual(JSON.parse(refreshes[0].opts.values.open_groups), ['2026-11-20'],
        'the replacement asks for open groups already filled');
    const base = polls.length;
    assert.equal(base, 1, 'the baseline check runs once on load');
    w.document.dispatchEvent(new w.CustomEvent('sm:runs-changed', { detail: {} }));
    await wait(700);
    assert.equal(polls.length, base + 1, 'a landed run makes the tree check at once (got ' + polls.length + ')');
    // a wake while a check is in flight is not lost
    hold = [];
    w.document.dispatchEvent(new w.CustomEvent('sm:runs-changed', { detail: {} }));
    await wait(700);
    assert.equal(polls.length, base + 2, 'the next wake starts a check');
    w.document.dispatchEvent(new w.CustomEvent('sm:runs-changed', { detail: {} }));
    await wait(700);
    assert.equal(polls.length, base + 2, 'no second check while one is in flight');
    const release = hold; hold = null; release.forEach((f) => f());
    await wait(800);
    assert.equal(polls.length, base + 3, 'the wake that came in flight runs once the check ends');
    scope.alive = false;
}

function highlightChecks() {
    const app = strip(fs.readFileSync(path.join(WEB, 'static', 'app.js'), 'utf8'));
    const a = app.indexOf('function _reapplyShownTreeHighlight(');
    assert.ok(a > 0, 'the re-mark after a tree swap is in app.js');
    const b = app.indexOf('\nfunction syncSidebarTreeHighlight(', a);
    const dom = new JSDOM(`<!doctype html><body>
      <div id="sidebar-tree"></div>
      <div id="inspector-pane"><div id="ds-detail-root" data-uid="f:2"></div></div></body>`,
      { runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    let scrolled = 0;
    w.Element.prototype.scrollIntoView = function () { scrolled++; };
    w._markActiveTreeBranch = function () {};
    w.eval('var CSS = window.CSS;\n' + app.slice(a, b));
    const tree = d.getElementById('sidebar-tree');
    const rebuild = () => {
        tree.innerHTML = '<details class="tree-date" open><summary>day</summary><ul>'
            + '<li><span class="tree-entry-click" data-uid="f:3">#3</span></li>'
            + '<li><span class="tree-entry-click" data-uid="f:2">#2</span></li></ul></details>'
            + '<details class="tree-date"><summary>older</summary><ul>'
            + '<li><span class="tree-entry-click" data-uid="f:1">#1</span></li></ul></details>';
        tree.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: tree } }));
    };
    rebuild();   // a run (#3) landed: the refetch drew it on top
    const act = () => Array.from(tree.querySelectorAll('.tree-entry-active')).map((e) => e.dataset.uid);
    assert.deepEqual(act(), ['f:2'], 'the marker stays on the run the detail pane shows (got ' + act() + ')');
    // the shown run sits in a closed group: marked, but nothing opens or scrolls
    d.getElementById('ds-detail-root').setAttribute('data-uid', 'f:1');
    rebuild();
    assert.deepEqual(act(), ['f:1'], 'a run in a closed group is marked too');
    assert.equal(tree.querySelectorAll('details')[1].open, false, 'its group is not opened');
    assert.equal(scrolled, 0, 'the tree is not scrolled');
    // no detail open: nothing is marked
    d.getElementById('inspector-pane').innerHTML = '';
    rebuild();
    assert.deepEqual(act(), [], 'no run shown: no marker');
}

(async () => {
    await wakeChecks();
    highlightChecks();
    console.log('sidebar_live_run_selfcheck ok');
    process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
