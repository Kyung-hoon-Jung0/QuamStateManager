/* v1.5.1 (r6 P2-7): one "Not done" expiry toast per two-press button.
 *
 * A two-press button that ran out of time says so ("Not done: the confirm
 * press did not come within 5 s ..."), and those toasts piled up: several
 * identical ones on screen at once. Now a new expiry of a button replaces
 * that button's previous toast (also when a panel re-render replaced the
 * button with its twin), pressing the button again takes its toast down,
 * and toasts of different buttons may coexist.
 *
 * The REAL app.js under jsdom with the REAL sync-panel markup (two armable
 * buttons: Take live and Discard my edits) rendered by the driver
 * (tests/test_v151_r6_expiry_toast.py). The arm countdown's 1 s interval is
 * run 50x faster; toast lifetimes keep their real clocks, so expiries land
 * while the previous toast is still up -- the pile-up the re-sweep saw.
 *
 * Run: node tests/v151_r6_expiry_toast_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs'), path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
    + '<div id="state-review-overlay"><div id="state-review-host">' + FX.panel + '</div></div>'
    + '<div id="table-pane"></div></body></html>',
    { url: 'http://localhost/qubits', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
const realSetInterval = w.setInterval.bind(w);
w.setInterval = (fn, ms) => realSetInterval(fn, Math.max(1, (ms || 0) / 50));
w.fetch = () => new Promise(() => {});
w.htmx = { ajax: () => Promise.resolve(), process() {}, trigger() {}, on() {}, config: {} };
w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
w.eval(APP);

const notDone = () => Array.from(d.querySelectorAll('#status-bar .toast'))
    .filter((t) => /Not done: the confirm press did not come/.test(t.textContent));
const take = () => d.querySelector('#state-review-host .sp-take.sync-arm');
const discard = () => d.querySelector('#state-review-host .sp-discard-all.sync-arm');
async function armAndExpire(btn) {
    w.SyncPanel.arm(btn, null, () => {});
    for (let t = 0; t < 3000 && btn.getAttribute('data-armed') === '1'; t += 10) await sleep(10);
    await sleep(20);
}

(async function () {
    ok(!!take() && !!discard(), 'the real panel has two two-press buttons (Take live, Discard my edits)');

    await armAndExpire(take());
    ok(notDone().length === 1, 'Take live ran out of time: one "Not done" toast');
    const first = notDone()[0];

    // the same button, again: its toast is replaced, not stacked
    w.SyncPanel.arm(take(), null, () => {});
    ok(!first.isConnected, 'pressing it again takes its "Not done" toast down');
    for (let t = 0; t < 3000 && take().getAttribute('data-armed') === '1'; t += 10) await sleep(10);
    await sleep(20);
    ok(notDone().length === 1, 'its second expiry leaves one toast, not two (' + notDone().length + ')');
    const second = notDone()[0];
    ok(second !== first, 'the newer toast is the one shown');

    // another button: its toast may stand beside the first
    await armAndExpire(discard());
    ok(notDone().length === 2, 'a different button\'s expiry keeps its own toast beside it (' + notDone().length + ')');

    // the panel re-renders: Take live is a new element, the same button
    const host = d.getElementById('state-review-host');
    host.innerHTML = FX.panel;
    await armAndExpire(take());
    ok(notDone().length === 2, 'after a re-render the same button still keeps one toast (' + notDone().length + ')');
    ok(!second.isConnected, 'its previous toast went');

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
