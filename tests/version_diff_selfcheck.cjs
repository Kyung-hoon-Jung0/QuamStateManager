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

    process.exit(fails ? 1 : 0);
})();
