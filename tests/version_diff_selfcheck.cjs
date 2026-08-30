/* jsdom selfcheck for the Versions panel's per-row Diff client wiring in
 * web/static/app.js (docs/128, and the review round that followed it).
 *
 * WHY THIS FILE EXISTS. The docs/128 tests pinned the client half by grepping
 * app.js for strings ("_diffGen", the away-guard selector, the smModalOpen
 * entry). This repo has a scar about exactly that shape — TestBookmarkMoved's
 * docstring records a pin that stayed green while the code containing it could
 * not execute — and the heavy review found the same weakness here: the token
 * could be renamed to a no-op and every test would still pass. So every
 * behaviour below is EXECUTED against the real shipped app.js.
 *
 * Run: node tests/version_diff_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
// The standing harness rule (CLAUDE.md): Node-realm eval does not expose
// window properties as bare globals — bridge every global the code reads bare
// or the miss is swallowed silently.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
// Node 24 makes some of these getter-only on globalThis; the older harnesses
// assign them from sloppy mode where the write silently no-ops. Under
// 'use strict' the same write throws, so define rather than assign.
function bridge(name, value) {
    try { global[name] = value; }
    catch (e) {
        try { Object.defineProperty(global, name, { value: value, configurable: true }); }
        catch (e2) { /* the realm already provides it — fine */ }
    }
}
bridge('navigator', window.navigator);
bridge('location', window.location);
// bare `history` — compare() calls history.pushState inside a try/catch, so
// an unbridged global fails SILENTLY and the sidebar-active assertions test
// nothing (found while writing section 9).
bridge('history', window.history);
const memStore = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
bridge('localStorage', memStore);
bridge('sessionStorage', memStore);
function bridgeWin(name, value) {
    try { window[name] = value; }
    catch (e) {
        try { Object.defineProperty(window, name, { value: value, configurable: true }); }
        catch (e2) { /* jsdom's own is fine */ }
    }
}
bridgeWin('localStorage', memStore);
bridgeWin('sessionStorage', memStore);
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

// A fetch we drive by hand: every call parks until the test resolves it, so
// the stale-response ordering is deterministic rather than a race.
const pending = [];
global.fetch = function (url) {
    return new Promise(function (resolve) { pending.push({ url: url, resolve: resolve }); });
};
window.fetch = global.fetch;
// app.js polls a few endpoints of its own on load, so count OUR requests only.
function diffCalls() {
    return pending.filter(function (p) {
        return String(p.url).indexOf('/state/versions/') === 0;
    });
}
function settle(i, html) {
    diffCalls()[i].resolve({ text: function () { return Promise.resolve(html); } });
}
function resetCalls() {
    for (let i = pending.length - 1; i >= 0; i--) {
        if (String(pending[i].url).indexOf('/state/versions/') === 0) pending.splice(i, 1);
    }
}

window.htmx = { ajax: function () { return Promise.resolve(); },
                trigger: function () {}, process: function () {} };
global.htmx = window.htmx;

// The base.html shell the module drives: the topbar chip + panel, and the
// body-level overlay pair. Markup copied in shape from base.html.
document.body.innerHTML = [
    '<button class="state-version-chip" aria-expanded="false">Versions</button>',
    '<div id="state-version-panel" hidden>',
    '  <ul class="state-versions-list"><li class="state-version-row">',
    '    <input type="checkbox" class="sv-check" value="20260821_010203_0001">',
    '  </li></ul>',
    '</div>',
    '<div id="version-diff-overlay" class="state-review-overlay" style="display:none">',
    '  <div class="state-review-backdrop"></div>',
    '  <div id="version-diff-host" class="state-review-host"></div>',
    '</div>',
    '<div id="table-pane"></div>',
].join('\n');

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = () => new Promise((r) => setTimeout(r, 0));

const SV = window.StateVersions;
const overlay = () => document.getElementById('version-diff-overlay');
const host = () => document.getElementById('version-diff-host');
const panel = () => document.getElementById('state-version-panel');

// A realistic partial: the ONE element naming which version this is, is a
// ts_local span, and it ships hidden until applyLocalTimes stamps it.
function partial(ts, extra) {
    return '<div class="state-review version-diff">'
        + '<h3>Version <span class="ts-local" data-utc="2026-08-21T01:02:03Z">'
        + '2026-08-21 01:02:03 UTC</span> &rarr; now</h3>'
        + '<div class="review-row" data-ts="' + ts + '">' + (extra || '') + '</div>'
        + '</div>';
}

(async function () {
    ok(SV && typeof SV.diff === 'function' && typeof SV.closeDiff === 'function',
       'StateVersions exports diff + closeDiff');

    // ---- 1. the request the button actually makes -----------------------
    SV.diff('20260821_010203_0001', 'CHIP_A');
    await tick();
    ok(diffCalls().length === 1, 'pressing Diff issues exactly one request');
    ok(diffCalls()[0].url === '/state/versions/20260821_010203_0001/diff?chip_key=CHIP_A',
       'the URL carries the ts AND the chip_key identity gate: ' + diffCalls()[0].url);
    ok(overlay().style.display === 'flex', 'the overlay is shown while it loads');
    ok(typeof overlay()._releaseTrap === 'function',
       'a focus trap is installed on the overlay');

    settle(0, partial('A'));
    await tick(); await tick();
    ok(/data-ts="A"/.test(host().innerHTML), 'the response is painted into the host');

    // ---- 2. the version identity is VISIBLE (review finding) ------------
    // A raw fetch+innerHTML fires no htmx swap event, so nothing else can
    // stamp the span: without the applyLocalTimes call the only text naming
    // the snapshot renders as a blank gap.
    const span = host().querySelector('.ts-local');
    ok(!!span, 'the partial carries the ts-local span naming the version');
    ok(span.hasAttribute('data-localized'),
       'the version timestamp is localized+revealed after injection (not left hidden)');
    ok((span.textContent || '').trim().length > 0,
       'the version timestamp has visible text');

    // ---- 3. smModalOpen knows this overlay (review finding, MAJOR) ------
    // Every global-shortcut gate keys off it; j/k/Enter and `?` fire behind
    // the modal if it does not.
    ok(window.smModalOpen() === true,
       'smModalOpen() reports a modal while the version-diff overlay is up');

    // ---- 4. the away guard keeps the panel, but only for OUR overlay ----
    SV.close();
    panel().hidden = true;
    SV.toggle();                       // opens the panel + installs the closer
    await tick();
    ok(panel().hidden === false, 'the versions panel is open');
    host().querySelector('.review-row').dispatchEvent(
        new window.MouseEvent('click', { bubbles: true }));
    await tick();
    ok(panel().hidden === false,
       'clicking INSIDE the version-diff overlay does not dismiss the panel underneath');
    document.getElementById('table-pane').dispatchEvent(
        new window.MouseEvent('click', { bubbles: true }));
    await tick();
    ok(panel().hidden === true, 'clicking elsewhere still dismisses the panel');

    // ---- 5. the stale-response token, exercised ------------------------
    // Row A is slow, row B is fast; A must not repaint over B.
    resetCalls();
    SV.diff('AAA', 'CHIP_A');
    await tick();
    SV.diff('BBB', 'CHIP_A');
    await tick();
    ok(diffCalls().length === 2, 'two presses issue two requests');
    settle(1, partial('B'));           // the SECOND press answers first
    await tick(); await tick();
    ok(/data-ts="B"/.test(host().innerHTML), 'the newer response paints');
    settle(0, partial('A'));           // the FIRST press answers late
    await tick(); await tick();
    ok(/data-ts="B"/.test(host().innerHTML),
       'the LATE response of the older press does NOT repaint over the newer one');

    // ---- 6. a response arriving after close must not paint -------------
    resetCalls();
    SV.diff('CCC', 'CHIP_A');
    await tick();
    SV.closeDiff();
    ok(overlay().style.display === 'none', 'closeDiff hides the overlay');
    settle(0, partial('C'));
    await tick(); await tick();
    ok(!/data-ts="C"/.test(host().innerHTML),
       'a response landing after closeDiff does not paint into the closed overlay');

    // ---- 7. no chip_key -> no query string (never an empty gate) -------
    resetCalls();
    SV.diff('DDD', '');
    await tick();
    ok(diffCalls()[0].url === '/state/versions/DDD/diff',
       'an absent chip_key sends no chip_key param at all: ' + diffCalls()[0].url);
    SV.closeDiff();

    // ---- 8. Compare routing (the 2 vs 3+ split) ------------------------
    const seen = [];
    window.htmx.ajax = function (m, url) { seen.push(url); return Promise.resolve(); };
    panel().innerHTML = ['20260821_010203_0001', '20260821_010204_0002',
                         '20260821_010205_0003']
        .map((t) => '<input type="checkbox" class="sv-check" value="' + t + '" checked>')
        .join('');
    SV.compare('CHIP_A');
    await tick();
    ok(seen.length === 1 && seen[0].indexOf('/diff/versions?') === 0,
       '3 ticks go to the differences-only table, not the Compare hub: ' + seen[0]);
    ok(seen[0].indexOf('chip_key=CHIP_A') > -1,
       'the N-way URL carries the chip identity too');
    ok(seen[0].indexOf('compare-hub') === -1, 'and never the hub');

    seen.length = 0;
    panel().innerHTML = ['20260821_010203_0001', '20260821_010204_0002']
        .map((t) => '<input type="checkbox" class="sv-check" value="' + t + '" checked>')
        .join('');
    SV.compare('CHIP_A');
    await tick();
    ok(seen.length === 1 && seen[0].indexOf('/diff/snapshots?') === 0,
       '2 ticks still open the docs/84 workbench: ' + seen[0]);

    // ---- 9. Compare lights the sidebar's Compare item (docs/132) -------
    // compare() pushState's manually, so no htmx history event fires — it
    // must call syncSidebarNavActive itself, and the matcher must map the
    // /diff/* entry routes onto the /diff link.
    document.body.insertAdjacentHTML('beforeend',
        '<nav class="sidebar-nav"><a href="/qubits">Qubits</a>' +
        '<a href="/diff">Compare</a></nav>');
    window.history.pushState({}, '', '/qubits');
    window.syncSidebarNavActive();
    ok(document.querySelector('.sidebar-nav a[href="/qubits"]')
        .classList.contains('active'), 'precondition: Qubits is active');
    panel().innerHTML = ['20260821_010203_0001', '20260821_010204_0002',
                         '20260821_010205_0003']
        .map((t) => '<input type="checkbox" class="sv-check" value="' + t + '" checked>')
        .join('');
    SV.compare('CHIP_A');
    await tick();
    ok(document.querySelector('.sidebar-nav a[href="/diff"]')
        .classList.contains('active'),
       'after a 3-tick Compare press, the sidebar Compare item is active');
    ok(!document.querySelector('.sidebar-nav a[href="/qubits"]')
        .classList.contains('active'),
       'and the previous page item is cleared');

    // ---- 10. the changes-only filter mode rides every refetch ----------
    ok(String(SV.setChanges).length > 0, 'setChanges is exported');
    global.localStorage = window.localStorage;   // the memStore stub
    var stored = {};
    bridge('localStorage', { getItem: function (k) { return stored[k] || null; },
                             setItem: function (k, v) { stored[k] = String(v); },
                             removeItem: function (k) { delete stored[k]; } });
    seen.length = 0;
    SV.more(80);
    await tick();
    ok(seen.length === 1
        && seen[0].indexOf('/state/versions?changes=only') === 0
        && seen[0].indexOf('limit=80') > -1,
       'paging carries the default changes=only mode: ' + seen[0]);
    seen.length = 0;
    stored['quam_versions_changes'] = 'all';
    SV.more(40);
    await tick();
    ok(seen[0].indexOf('/state/versions?changes=all') === 0,
       'a stored "all" choice rides the refetch: ' + seen[0]);

    // ---- 10b. the live refresh preserves an expanded page (docs/132
    // review: a bare refetch collapsed "Show more" back to 40 and silently
    // dropped Compare ticks beyond the first page) --------------------
    delete stored['quam_versions_changes'];
    panel().hidden = false;
    var many = [];
    for (var i2 = 0; i2 < 50; i2++) {
        many.push('<input type="checkbox" class="sv-check" value="20260821_0102'
            + String(10 + i2) + '_0001">');
    }
    panel().innerHTML = many.join('');
    seen.length = 0;
    document.body.dispatchEvent(new window.CustomEvent('stateHistoryChanged',
        { bubbles: true }));
    await new Promise(function (r) { setTimeout(r, 1000); });   // 900ms debounce
    ok(seen.length === 1 && seen[0].indexOf('limit=50') > -1,
       'the stateHistoryChanged refresh keeps the expanded 50-row page: '
       + seen[0]);

    // ---- 11. per-value take (docs/132 #7) ------------------------------
    window.__chipToken = 'CHIPTOK';
    host().innerHTML =
        '<div class="review-row" data-dot-path="qubits.q1.f_01"' +
        ' data-value="6100000000.0" data-create="0"' +
        " data-prev='6200000000.0'>" +
        '<span class="review-old sv-take-src">6,100,000,000.0</span>' +
        '<button type="button" class="btn-xs sv-take"' +
        ' onclick="StateVersions.take(this)">✓ accept</button>' +
        '<button type="button" class="btn-xs sv-take-edit"' +
        ' onclick="StateVersions.editTake(this)">✎ edit</button></div>';
    resetCalls();
    var takeBtn = host().querySelector('.sv-take');
    var posted = [];
    global.fetch = window.fetch = function (url, opts) {
        posted.push({ url: url, opts: opts });
        return new Promise(function (resolve) {
            posted[posted.length - 1].resolve = resolve;
        });
    };
    SV.take(takeBtn);
    await tick();
    ok(posted.length === 1 && posted[0].url === '/field/edit-batch',
       'take posts to the one edit door: ' + (posted[0] && posted[0].url));
    var body = JSON.parse(posted[0].opts.body);
    ok(body.updates && body.updates.length === 1
        && body.updates[0].dot_path === 'qubits.q1.f_01'
        && body.updates[0].value === 6100000000.0
        && body.updates[0].create === false,
       'the POST body carries dot_path + the JSON value + create');
    ok(body.expect_chip === 'CHIPTOK',
       'expect_chip rides along (docs/120 — unlike the old reviewAccept)');
    ok(takeBtn.disabled === true, 'the button locks while in flight');
    // a FAILED response must unlock and NOT mark the row
    posted[0].resolve({ json: function () {
        return Promise.resolve({ ok: false, results: [{ error: 'nope' }] });
    } });
    await tick(); await tick();
    ok(takeBtn.disabled === false, 'a failed take unlocks the button');
    ok(!host().querySelector('.review-row').classList.contains('review-accepted'),
       'a failed take does not mark the row accepted');
    // a SUCCESSFUL take marks the row and swaps the tray via the choke point
    var traySwaps = [];
    window._swapPendingTray = function (html) { traySwaps.push(html); };
    posted.length = 0;
    SV.take(takeBtn);
    await tick();
    posted[0].resolve({ json: function () {
        return Promise.resolve({ ok: true, tray_html: '<div id="pending-tray"></div>',
                                 results: [{ dot_path: 'qubits.q1.f_01', applied: true }] });
    } });
    await tick(); await tick();
    ok(host().querySelector('.review-row').classList.contains('review-accepted'),
       'a successful take marks the row accepted');
    ok(traySwaps.length === 1,
       'the Review tray is swapped through the single choke point');

    // ---- 12. the RAM undo stack (docs/132 r5) --------------------------
    // The successful take above recorded {prev: 6200000000.0}. Ctrl+Z must
    // restore prev with ONE POST, unmark the row, and preempt the global
    // docs/107 chain; Ctrl+Shift+Z re-applies.
    var overlayEl = document.getElementById('version-diff-overlay');
    overlayEl.style.display = 'flex';
    function ctrlZ(shift) {
        document.body.dispatchEvent(new window.KeyboardEvent('keydown',
            { key: 'z', ctrlKey: true, shiftKey: !!shift, bubbles: true,
              cancelable: true }));
    }
    posted.length = 0;
    ctrlZ(false);
    await tick();
    ok(posted.length === 1, 'Ctrl+Z with a recorded take posts once');
    var ub = JSON.parse(posted[0].opts.body);
    ok(ub.updates[0].dot_path === 'qubits.q1.f_01'
        && ub.updates[0].value === 6200000000.0,
       'undo restores the PREV working value the row itself displayed');
    posted[0].resolve({ json: function () {
        return Promise.resolve({ ok: true, tray_html: '<div id="pending-tray"></div>',
                                 results: [{ dot_path: 'qubits.q1.f_01' }] });
    } });
    await tick(); await tick();
    ok(!host().querySelector('.review-row').classList.contains('review-accepted'),
       'undo un-marks the accepted row');
    ok(host().querySelector('.sv-take').textContent === '✓ accept',
       'undo restores the accept label: '
       + host().querySelector('.sv-take').textContent);

    posted.length = 0;
    ctrlZ(true);                     // redo
    await tick();
    ok(posted.length === 1 && JSON.parse(posted[0].opts.body)
        .updates[0].value === 6100000000.0,
       'Ctrl+Shift+Z re-applies the taken value');
    posted[0].resolve({ json: function () {
        return Promise.resolve({ ok: true, results: [{}] });
    } });
    await tick(); await tick();
    ok(host().querySelector('.review-row').classList.contains('review-accepted'),
       'redo re-marks the row');

    // scope: with the overlay closed and no workbench takes, Ctrl+Z is NOT
    // consumed here (the docs/107 global chain owns it)
    overlayEl.style.display = 'none';
    posted.length = 0;
    ctrlZ(false);
    await tick();
    // docs/141 4ac: count the TAKE DOOR, not every request the page makes.
    // 4p's popup-poll baseline fires ~1.5 s after load, which is about when
    // this assertion runs, so `posted.length` was a coin flip on an unrelated
    // background fetch.
    var strayTakes = posted.filter(function (r) { return /field\/edit/.test(r.url || r); });
    ok(strayTakes.length === 0,
       'out of scope, the RAM stack never consumes the press');
    overlayEl.style.display = 'flex';

    // ---- 13. edit-before-accept ---------------------------------------
    var editBtn = host().querySelector('.sv-take-edit');
    SV.editTake(editBtn);
    var einput = host().querySelector('.sv-take-input');
    ok(!!einput, 'edit swaps the version value for an inline input');
    ok(einput.value === '6,100,000,000.0',
       'the input starts from the displayed (round-trip-exact) text: '
       + einput.value);
    einput.value = '6150000000.0';
    var takeBtn2 = host().querySelector('.sv-take');
    takeBtn2.disabled = false;       // fresh press
    posted.length = 0;
    SV.take(takeBtn2);
    await tick();
    ok(posted.length === 1 && JSON.parse(posted[0].opts.body)
        .updates[0].value === '6150000000.0',
       'accept posts the EDITED text (the server parses it for the target)');
    posted[0].resolve({ json: function () {
        return Promise.resolve({ ok: true, results: [{}] });
    } });
    await tick(); await tick();

    process.exit(fails ? 1 : 0);
})();
