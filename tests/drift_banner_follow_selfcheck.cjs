/* QA diagnostics-r2-11 -- an open page learns that the live chip moved,
 * without F5. Re-scoped 2026-09-26 to the surface that carries it now.
 *
 * Original bug (real Chrome): an outside write to the rig chip with
 * /diagnostics open -- the pill turned "Live chip moved" within 8 s, but the
 * full-width banner offering Review & sync / Take live / Keep mine never came
 * (38 s watched) although /state/drift said live_diverged:true throughout.
 * 986fd19 made the drift poll re-render #live-diverged-slot.
 *
 * sync-ux 2026-09-25 (user decision, ef07a90) removed that banner: the ONE
 * status control in the top bar (#pending-tray, "Live chip changed · N values"
 * with its sync panel) carries the signal and the choices. The guarantee is
 * unchanged -- an open page on a CLEAN chip follows an outside write on the
 * next drift poll, and a verdict back to clean lowers it again -- so this pin
 * now drives the real poll (window._pollDrift) through the real app.js and
 * asserts the control is re-rendered, and that nothing still polls the
 * retired banner endpoint (it renders nothing; a request to it would be a
 * dead round-trip on every poll that hides the loss of the real repaint).
 *
 * Run: node tests/drift_banner_follow_selfcheck.cjs   (needs jsdom)
 */

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error("Cannot find module 'jsdom'"); process.exit(2); }

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/diagnostics', pretendToBeVisual: true,
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
let payload = null;
const fake = () => Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
global.fetch = fake;
window.fetch = fake;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
const calls = [];
window.htmx = { ajax: (m, url, o) => { calls.push([m, url, o && o.target]); return Promise.resolve(); },
                process: () => {}, trigger: () => {} };
global.htmx = window.htmx;

const doc = window.document;
// a CLEAN chip: the control says "In sync" (the r2-11 case -- nothing unapplied)
doc.body.innerHTML =
    '<div id="pending-tray" data-change-count="0" data-edit-seq="E1" data-sync-sig="SYNCED"'
    + ' data-sync-state="synced"><span class="sync-control sync-st-synced">'
    + '<button class="sync-control-main state-status-badge state-status-synced">'
    + '<span class="sync-control-text">In sync</span></button></span></div>'
    + '<div id="live-diverged-slot"></div>';

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try { window.eval(src); } catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const trayGets = () => calls.filter((c) => c[1] === '/state/tray');
const bannerGets = () => calls.filter((c) => c[1] === '/state/diverged-banner');
const tray = () => doc.getElementById('pending-tray');
// what the /state/tray swap would leave behind (the control's class follows its state)
const setTray = (sig, state) => {
    tray().setAttribute('data-sync-sig', sig);
    const b = tray().querySelector('.sync-control-main');
    b.className = 'sync-control-main state-status-badge state-status-' + state;
};
const poll = async (p) => { payload = p; window._pollDrift(); await sleep(40); };

(async function main() {
    ok(typeof window._pollDrift === 'function', 'setup: the drift poll is reachable');
    // first poll only records edit_seq; same signature -> nothing
    await poll({ ok: true, tracked: true, count: 0, live_diverged: false, edit_seq: 'E1',
                 sync: { state: 'synced', sig: 'SYNCED', stale: {} } });
    ok(trayGets().length === 0, 'r2-11: an unchanged verdict re-renders nothing');

    // the outside write: live moved on a clean chip
    await poll({ ok: true, tracked: true, count: 0, live_diverged: true, edit_seq: 'E1',
                 sync: { state: 'live', sig: 'LIVE3', stale: {} } });
    ok(trayGets().length === 1 && trayGets()[0][2] === '#pending-tray',
       'r2-11: a drift poll reporting the live chip moved re-renders the status control ('
       + JSON.stringify(calls) + ')');

    // the control now shows it; the next poll must not re-fetch it again
    setTray('LIVE3', 'live');
    await poll({ ok: true, tracked: true, count: 0, live_diverged: true, edit_seq: 'E1',
                 sync: { state: 'live', sig: 'LIVE3', stale: {} } });
    ok(trayGets().length === 1, 'r2-11: a control already showing it is not re-fetched on every poll');

    // back to clean (another window took live): lowered the same way
    await poll({ ok: true, tracked: true, count: 0, live_diverged: false, edit_seq: 'E1',
                 sync: { state: 'synced', sig: 'SYNCED2', stale: {} } });
    ok(trayGets().length === 2, 'r2-11: a verdict back to clean lowers the control the same way');

    // not while an apply is in flight (its own response repaints the tray)
    setTray('SYNCED2', 'synced');
    window._applyInFlight = true;
    await poll({ ok: true, tracked: true, count: 0, live_diverged: true, edit_seq: 'E1',
                 sync: { state: 'live', sig: 'LIVE4', stale: {} } });
    window._applyInFlight = false;
    ok(trayGets().length === 2, 'r2-11: not while an apply is in flight');

    ok(bannerGets().length === 0,
       'sync-ux: the poll never requests the retired banner endpoint (' + JSON.stringify(bannerGets()) + ')');
    ok(typeof window._onDriftDivergedBanner === 'undefined',
       'sync-ux: the retired banner hook is gone, not left as dead code a later merge could re-wire');
    console.log(fails ? 'FAILED ' + fails : 'drift_banner_follow_selfcheck: all checks passed');
    process.exit(fails ? 1 : 0);
})();
