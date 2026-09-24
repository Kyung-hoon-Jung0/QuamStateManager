/* QA r2-02 + r2-03 — Pin & Browse, driven through the REAL app.js under jsdom.
 *
 * r2-02: in pin mode the beforeSwap interceptor cancels htmx's swap and builds
 * the two-column split itself, so htmx:afterSwap never fires -- and that was
 * the ONLY thing clearing the 200 ms "Loading #id…" chip. It stayed forever
 * over the right column's run number. After a pinned step the chip must be
 * gone (both the split branch and the same-run branch), while an ordinary
 * slow load still shows it.
 *
 * r2-03: the interceptor compared the bare run NUMBER, so KH #22 counted as
 * "the pinned KRISS #22 clicked again" and was dropped silently. Run ids
 * restart in every folder; the identity is (folder, run id).
 *
 * Run: node tests/pin_compare_selfcheck.cjs  (driven by tests/test_pin_compare.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<ul class="tree-entries">' +
    '<li><span class="tree-entry-click" data-uid="kh:4112" data-run-id="4112" tabindex="0">#4112</span></li>' +
    '<li><span class="tree-entry-click" data-uid="kh:4113" data-run-id="4113" tabindex="0">#4113</span></li>' +
    '<li><span class="tree-entry-click" data-uid="bbbb:22" data-run-id="22" tabindex="0">#22</span></li>' +
    '</ul><div id="table-pane"></div><div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

window.fetch = function () { return new Promise(function () {}); };
const loads = [];
window.htmx = { ajax: function (m, url) { loads.push(url); return Promise.resolve(); },
                on: function () {}, trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const pane = document.getElementById('inspector-pane');
function detail(folder, run) {
    return '<div id="ds-detail-root" data-uid="' + folder + ':' + run + '" data-folder-key="' + folder +
           '" data-run-id="' + run + '"><div class="inspector-header"><span class="inspector-runid">#' + run +
           '</span><button id="inspector-pin-btn"></button><button class="inspector-close"></button></div></div>';
}
function serverSwap(html) {
    // what htmx does with a 200 response: beforeSwap on the target, and only
    // when nobody cancelled it, the swap itself + afterSwap
    const ev = new window.CustomEvent('htmx:beforeSwap', { bubbles: true, cancelable: true,
        detail: { target: pane, shouldSwap: true, serverResponse: html } });
    pane.dispatchEvent(ev);
    if (ev.detail.shouldSwap) {
        pane.innerHTML = html;
        pane.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
    }
    return ev.detail;
}
function tree(uid) { document.querySelector('.tree-entry-click[data-uid="' + uid + '"]').click(); }
function chip() { return pane.classList.contains('ds-slow-loading') || pane.hasAttribute('data-loading-run'); }

(async function main() {
    // an ordinary slow load still shows the chip (the intended feedback)
    tree('kh:4113');
    await sleep(260);
    ok(chip() && pane.getAttribute('data-loading-run') === '#4113',
       'a load still in flight after 200ms shows "Loading #4113…"');
    serverSwap(detail('kh', '4113'));
    ok(!chip(), 'an ordinary swap clears it (afterSwap)');

    // ── r2-02: pin #4113, step to #4112 -> the split, and NO stuck chip ──
    window.togglePinDataset();
    ok(window._pinnedRunId === '4113', '(fixture) #4113 pinned');
    tree('kh:4112');
    await sleep(260);                       // a slow response: the chip is up
    let d = serverSwap(detail('kh', '4112'));
    ok(d.shouldSwap === false && !!pane.querySelector('.inspector-split'),
       'the pinned step builds the two-column split');
    await sleep(260);
    ok(!chip(), 'r2-02: the "Loading #4112…" chip is gone once the split rendered');
    tree('kh:4112');                        // a FAST response: the timer must not fire later
    serverSwap(detail('kh', '4112'));
    await sleep(260);
    ok(!chip(), 'r2-02: a fast pinned step never raises the chip either');
    tree('kh:4113');                        // back to the pinned run: the same-run branch
    await sleep(260);
    d = serverSwap(detail('kh', '4113'));
    ok(d.shouldSwap === false && !!pane.querySelector('.inspector-split'),
       'the pinned run clicked again keeps the split');
    await sleep(10);
    ok(!chip(), 'r2-02: the same-run branch clears the chip too');

    // ── r2-03: the same run NUMBER from another folder is a different run ──
    window.unpinDataset();
    pane.innerHTML = detail('aaaa', '22');
    window.togglePinDataset();
    ok(window._pinnedRunId === '22', '(fixture) aaaa #22 pinned');
    tree('bbbb:22');
    d = serverSwap(detail('bbbb', '22'));
    const cols = pane.querySelectorAll('.inspector-split > div');
    const right = pane.querySelector('.inspector-current-col #ds-detail-root');
    const left = pane.querySelector('.inspector-pinned-col #pinned-ds-detail-root');
    ok(!!pane.querySelector('.inspector-split') && right && right.getAttribute('data-uid') === 'bbbb:22'
       && left && left.getAttribute('data-uid') === 'aaaa:22',
       'r2-03: bbbb #22 opens BESIDE the pinned aaaa #22 (was dropped silently)');
    d = serverSwap(detail('aaaa', '22'));
    ok(d.shouldSwap === false && pane.querySelector('.inspector-current-col #ds-detail-root')
       .getAttribute('data-uid') === 'bbbb:22',
       'the pinned run itself clicked again is still suppressed (Round-15 item 5)');
    window.unpinDataset();
    ok(window._pinnedRunKey === null, 'unpin clears the folder-aware pin key');

    console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
