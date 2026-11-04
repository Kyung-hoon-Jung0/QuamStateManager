/* The drift poll's guards (final walk R-d/R-g/F23), EXECUTED against the real
 * shipped app.js under jsdom. Every bare global the code reads is the realm's own.
 *
 *  H. a window that missed a pull step (two pulls between two of its polls)
 *     names the UNION of every step's lost values, not only the latest.
 *  C. a chip switch, or a lower step on the same chip (server restart), is
 *     not a step: no toast.
 *  R. a tray re-render that never settles does not hold the verdict off for ever.
 *  W. an own write left pending does not hold the verdict off for ever.
 *  T. the drift fetch carries an AbortSignal, is aborted after its timeout, and
 *     the next poll asks again.
 *
 * Run: node tests/sync_poll_guard_selfcheck.cjs  (driven by tests/test_sync_review_applied.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function world() {
    const dom = new JSDOM('<!doctype html><html><head></head><body>'
        + '<div id="pending-tray" data-sync-sig="old" data-sync-state="synced"></div></body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/bulk' });
    const w = dom.window;
    const S = { ajax: 0, lost: [], fetches: [] };
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: function () { S.ajax++; return new w.Promise(function () {}); },
               trigger: function () {}, process: function () {}, on: function () {}, config: {} };
    w.fetch = function () { return new w.Promise(function () {}); };
    w.LiveEditUndo = { record: function () {}, clear: function () {}, _updateTrayBtn: function () {} };
    w.eval(APP);
    w._smSayLost = function (l) { S.lost.push(l); };
    w._followValuesOnScreen = function () {};
    let now = 1e12;
    w.Date.now = function () { return now; };
    return { w: w, S: S, advance: function (ms) { now += ms; } };
}

/* H: the union of missed steps */
{
    const { w, S } = world();
    w._onDriftPullSeq({ pull_seq: 'C:/x#1', pull_lost: [] });
    const r = w._onDriftPullSeq({ pull_seq: 'C:/x#3', pull_lost: [],
        pull_lost_hist: [[2, [{ path: 'qubits.q1.T1' }]], [3, []]] });
    ok(r === true, 'H1 a two-step jump is a step');
    const paths = (S.lost[0] || []).map(function (x) { return x.path; });
    ok(paths.indexOf('qubits.q1.T1') >= 0, 'H2 the first missed step\'s loss is named: ' + JSON.stringify(paths));
}
/* C: chip switch and restart */
{
    const { w, S } = world();
    w._onDriftPullSeq({ pull_seq: 'C:/x#5', pull_lost: [] });
    const r = w._onDriftPullSeq({ pull_seq: 'C:/y#1', pull_lost: [{ path: 'p' }] });
    ok(r === false && S.lost.length === 0, 'C1 a chip switch is not a step');
    const { w: w2, S: S2 } = world();
    w2._onDriftPullSeq({ pull_seq: 'C:/x#5', pull_lost: [] });
    const r2 = w2._onDriftPullSeq({ pull_seq: 'C:/x#2', pull_lost: [{ path: 'p' }] });
    ok(r2 === false && S2.lost.length === 0, 'C2 a lower step (server restart) is not a step');
}
/* R: a stuck tray refresh */
{
    const { w, S, advance } = world();
    ok(w._onDriftSyncSig({ sync: { sig: 'a' } }) === true && S.ajax === 1, 'R1 a new verdict re-renders the tray');
    ok(w._onDriftSyncSig({ sync: { sig: 'b' } }) === false && S.ajax === 1, 'R2 while it runs, no second render');
    advance(6500);
    w._onDriftSyncSig({ sync: { sig: 'b' } });
    ok(S.ajax === 2, 'R3 a render stuck past 6 s does not hold the verdict off (ajax=' + S.ajax + ')');
}
/* W: a stuck own write */
{
    const { w, advance } = world();
    w._applyInFlight = true;
    ok(w._ownWriteStillPending(null) === true, 'W1 an own write in flight holds the verdict');
    advance(21000);
    ok(w._ownWriteStillPending(null) === false, 'W2 ...but not past 20 s');
}
/* T: the drift fetch timeout */
{
    const { w, advance } = world();
    const inits = [];
    w.fetch = function (u, init) { inits.push(init || {}); return new w.Promise(function () {}); };
    const timers = [];
    const realST = w.setTimeout;
    w.setTimeout = function (fn, ms) { if (ms === 15000) { timers.push(fn); return 0; } return realST(fn, ms); };
    w._pollDrift();
    const sig = inits[0] && inits[0].signal;
    ok(!!sig && typeof sig.aborted === 'boolean', 'T1 the drift fetch carries an AbortSignal');
    timers.forEach(function (f) { f(); });
    ok(!!sig && sig.aborted === true, 'T2 the timeout aborts it');
    advance(17000);
    w._pollDrift();
    ok(inits.length === 2, 'T3 the next poll asks again (fetches=' + inits.length + ')');
    w.setTimeout = realST;
}

console.log((fails ? 'FAILED ' : 'PASS ') + (asserts - fails) + '/' + asserts + ' assertions');
process.exit(fails ? 1 : 0);
