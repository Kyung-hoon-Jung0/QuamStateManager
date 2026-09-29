/* jsdom selfcheck: the Pulses page's "Delete together with ..." (w8, docs/218
 * open issue). Loads the REAL app.js and pulses.js into one window.
 *   1. the lab refusal body swaps into #pulse-delete-result -- only that body,
 *      only a 400, and only into the detail whose own delete form sent it
 *   2. the press posts ONE /field/edit-batch: every listed path as a delete
 *      row, group "new" (one Ctrl+Z), the page's chip token
 *   3. a Ctrl+Z pressed while the batch is still being checked waits for it
 *      (UndoQueue.holdWhile) and then undoes IT
 *   4. success: the pane says what went (a "Deleted <name>" toast naming the
 *      pulse to re-open), the table refreshes (pulses-changed), the tray swaps
 *   5. Ctrl+Z's answer restoring an ANCESTOR (the pulse went inside a deleted
 *      gate) re-opens the pulse
 *   6. a batch the check still refuses: nothing on the pane changes, the
 *      reason is shown in place, and paths the check names are ADDED to the
 *      offer (the button stays live)
 *   7. a batch that went through unchecked (lab code could not run) says so
 *   8. a pane that moved on to another pulse is left alone
 * Run: node tests/pulses_delete_together_selfcheck.cjs
 *      (driven by tests/test_pulse_delete_together.py)
 */
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

const SLOT = 'qubit_pairs.q1-2.macros.cz_SNZ.flux_pulse_target';
const GATE = 'qubit_pairs.q1-2.macros.cz_SNZ';
const OP = 'qubits.q2.z.operations.cz_SNZ_flux_pulse_q2_q1';
const EXTRA = 'qubits.q9.z.operations.extra';

function detailHtml(p) {
    return '<div id="pulse-detail-root" data-pulse-path="' + p + '">'
        + '<div class="pulse-delete-confirm">'
        + '<form id="delform"><input type="hidden" name="path" value="' + p + '"></form>'
        + '<div id="pulse-delete-result" class="pulse-delete-result"></div></div>'
        + '<div id="pulse-detail-plot"></div></div>';
}
function refusedHtml(together) {
    return '<div class="pulse-delete-refused" data-refused-path="' + together[0] + '"'
        + " data-together='" + JSON.stringify(together) + "'>"
        + '<p class="pulse-delete-refused-msg">Your chip\'s own generate_config() refused this</p>'
        + '<div class="pulse-together"><ul class="pulse-together-list"></ul>'
        + '<button type="button" class="btn-sm pulse-delete-together">Delete together with 1 op</button>'
        + '<p class="pulse-together-status" hidden></p></div></div>';
}

const dom = new JSDOM('<!doctype html><html><head></head><body>'
    + '<div id="pending-tray" data-change-sig="s0"></div>'
    + '<div id="inspector-pane">' + detailHtml(SLOT) + '</div></body></html>',
    { url: 'http://localhost/pulses', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;

const ajaxCalls = [], triggers = [], toasts = [];
window.htmx = {
    ajax: function (method, url, opts) { ajaxCalls.push({ method: method, url: url, opts: opts }); return Promise.resolve(); },
    trigger: function (el, name) { triggers.push(name); },
    process: function () {},
    swap: function () {},
};
global.htmx = window.htmx;
// fetch: every call is recorded and answered by the test (a deferred)
const fetches = [];
function deferred() { let res, rej; const p = new Promise((a, b) => { res = a; rej = b; }); return { p: p, res: res, rej: rej }; }
window.fetch = function (url, opts) {
    const d = deferred();
    fetches.push({ url: String(url), opts: opts || {}, d: d });
    return d.p;
};
global.fetch = window.fetch;
function answer(f, status, body) {
    f.d.res({ ok: status < 300, status: status,
              json: () => Promise.resolve(body), clone: function () { return this; } });
}

try {
    window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    window.eval(fs.readFileSync(path.join(STATIC, 'pulses.js'), 'utf8'));
} catch (e) {
    console.error('FAIL: app.js / pulses.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}
window.showToast = function (msg, level) { toasts.push({ msg: msg, level: level }); };
// In a browser app.js's top-level `function _swapPendingTray` IS window's
// (a classic script's declarations are global properties; lab-check.js leans
// on the same). jsdom's window.eval does not expose them: bridge it.
const traySwaps = [];
window._swapPendingTray = function (html) {
    traySwaps.push(html);
    const s = window.document.getElementById('pending-tray');
    if (s) s.outerHTML = html;
};

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const d = window.document;
function pressCtrlZ() {
    const ev = new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
    d.dispatchEvent(ev);
}
function undoPosts() { return ajaxCalls.filter((c) => c.method === 'POST' && /^\/undo/.test(c.url)).length; }
// htmx 2.0.4 exactly: after HX-Retarget, beforeSwap is raised on the ORIGINAL
// target (#inspector-pane) with detail.elt = that element and detail.target =
// the retargeted slot -- the requesting form is NOT in the event (a first cut
// keyed on detail.elt and swapped nothing in real Chrome)
function beforeSwap(target, status, text) {
    const pane = d.getElementById('inspector-pane');
    const det = { target: target, elt: pane, xhr: { status: status, responseText: text },
                  shouldSwap: false, isError: true, requestConfig: { path: '/api/pulse/delete' } };
    pane.dispatchEvent(new window.CustomEvent('htmx:beforeSwap', { detail: det, bubbles: true, cancelable: true }));
    return det;
}
function freshRefusal(together) {
    const pane = d.getElementById('inspector-pane');
    pane.innerHTML = detailHtml(together[0]);
    d.getElementById('pulse-delete-result').innerHTML = refusedHtml(together);
    return d.querySelector('.pulse-delete-together');
}

(async function main() {
    // ── 1. the swap allowance ────────────────────────────────────────────
    {
        const slot = d.getElementById('pulse-delete-result');
        const body = refusedHtml([SLOT, OP]);
        let det = beforeSwap(slot, 400, body);
        ok(det.shouldSwap === true && det.isError === false,
           '1: a lab refusal of THIS pulse\'s delete swaps into its delete step');
        det = beforeSwap(slot, 400, '<div class="toast"><p>other error</p></div>');
        ok(det.shouldSwap === false && det.isError === true, '1: any other 400 body stays a toast');
        det = beforeSwap(slot, 409, body);
        ok(det.shouldSwap === false, '1: only a 400 is let through');
        det = beforeSwap(slot, 400, refusedHtml([OP, SLOT]));   // another pulse's refusal
        ok(det.shouldSwap === false && det.isError === true,
           '1: a refusal of ANOTHER pulse never lands in this pulse\'s slot (it stays a toast)');
        d.querySelector('.pulse-delete-confirm').hidden = true;
        det = beforeSwap(slot, 400, body);
        ok(det.shouldSwap === false && det.isError === true,
           '1: a delete step closed meanwhile keeps the toast (never swapped out of sight)');
        d.querySelector('.pulse-delete-confirm').hidden = false;
        const esc = 'qubit_pairs.a&amp;b.macros.g.x';
        d.getElementById('pulse-detail-root').setAttribute('data-pulse-path', 'qubit_pairs.a&b.macros.g.x');
        det = beforeSwap(slot, 400, body.replace('data-refused-path="' + SLOT + '"', 'data-refused-path="' + esc + '"'));
        ok(det.shouldSwap === true, '1: the refused path is read HTML-unescaped (Jinja escapes the attribute)');
        d.getElementById('pulse-detail-root').setAttribute('data-pulse-path', SLOT);
    }

    // ── 2-4. the press, the held Ctrl+Z, success ─────────────────────────
    {
        window.__chipToken = 'chipA';
        const btn = freshRefusal([SLOT, OP]);
        window.PulsesPage.deleteTogether(btn);
        const f = fetches[fetches.length - 1];
        ok(f && f.url === '/field/edit-batch' && String(f.opts.method).toUpperCase() === 'POST',
           '2: the press posts to /field/edit-batch');
        const body = JSON.parse(f.opts.body);
        ok(JSON.stringify(body.updates) === JSON.stringify([
            { dot_path: SLOT, 'delete': true }, { dot_path: OP, 'delete': true }]),
           '2: every listed path rides as a delete row, the pulse first');
        ok(body.group === 'new' && body.expect_chip === 'chipA',
           '2: one Ctrl+Z group, and the page\'s chip token');
        ok(btn.disabled === true, '2: the button is disabled while the check runs');
        ok(/Checking the whole batch/.test(d.querySelector('.pulse-together-status').textContent),
           '2: the step says the batch is being checked');
        const n0 = undoPosts();
        pressCtrlZ();
        await sleep(300);
        ok(undoPosts() === n0 && window.UndoQueue.depth() === 1,
           '3: a Ctrl+Z pressed during the check is HELD, not sent');
        answer(f, 200, { ok: true, tray_html: '<div id="pending-tray" data-change-sig="s1"></div>',
                         results: [{ dot_path: SLOT, applied: true, deleted: true },
                                   { dot_path: OP, applied: true, deleted: true }] });
        await sleep(400);
        ok(undoPosts() === n0 + 1, 'a Ctrl+Z pressed while the batch is checked waits for it');
        const up = ajaxCalls.filter((c) => c.method === 'POST' && /^\/undo/.test(c.url)).pop();
        // released on the answer's ARRIVAL, the press declared the old tray
        // ("s0") and the server refused it as another window's (real Chrome)
        ok(up && up.opts && up.opts.values && up.opts.values.expect_sig === 's1',
           '3: and declares the tray the batch left (its signature), not the one before it');
        const toast = d.querySelector('#inspector-pane .toast.toast-success');
        ok(!!toast && /^Deleted flux_pulse_target together with 1 other path \(/.test(toast.textContent.trim()),
           '4: the pane says what went, the ordinary "Deleted <name>" way');
        ok(toast && toast.getAttribute('data-reopen-path') === SLOT, '4: the toast names the pulse to re-open');
        ok(!d.getElementById('pulse-detail-root'), '4: the deleted pulse\'s detail is no longer shown');
        ok(triggers.indexOf('pulses-changed') >= 0, '4: the table (rows, count, used_by) refreshes');
        ok(d.getElementById('pending-tray').getAttribute('data-change-sig') === 's1', '4: the tray swapped');
    }

    // ── 5. Ctrl+Z restoring an ANCESTOR re-opens the pulse ───────────────
    {
        const n0 = ajaxCalls.length;
        d.dispatchEvent(new window.CustomEvent('cellsReverted', { detail: {
            message: 'Undone', entries: [{ dot_path: OP, deleted: true },
                                         { dot_path: GATE, deleted: true }] } }));
        await sleep(50);
        const re = ajaxCalls.slice(n0).filter((c) => c.method === 'GET'
            && c.url === '/pulse/detail?path=' + encodeURIComponent(SLOT));
        ok(re.length === 1, '5: the undo re-opens the pulse that went inside the gate');
    }

    // ── 6. refused as a whole: in place, widened, still live ─────────────
    {
        const btn = freshRefusal([SLOT, OP]);
        const paneBefore = d.getElementById('inspector-pane').innerHTML;
        triggers.length = 0;
        window.PulsesPage.deleteTogether(btn);
        const f = fetches[fetches.length - 1];
        answer(f, 400, { ok: false, lab_refused: true,
                         error: 'Your gate refused this value -- nothing was written: KeyError',
                         lab_delete_also: [OP, EXTRA] });
        await sleep(50);
        const box = d.querySelector('.pulse-delete-refused');
        ok(!!box && JSON.parse(box.getAttribute('data-together')).join('|') === [SLOT, OP, EXTRA].join('|'),
           '6: what the check names is added to the offer');
        ok(!!d.querySelector('.pulse-together-list li[data-kind="more"]') &&
           d.querySelector('.pulse-together-list li[data-kind="more"] code').textContent === EXTRA,
           '6: and listed');
        ok(btn.disabled === false && btn.textContent === 'Delete all 3 together', '6: the button stays live, relabelled');
        ok(/^✗ Nothing was deleted: Your gate refused/.test(d.querySelector('.pulse-together-status').textContent),
           '6: the reason is shown in place');
        ok(triggers.indexOf('pulses-changed') < 0, '6: nothing refreshes -- nothing was written');
        ok(!!d.getElementById('pulse-detail-root'), '6: the detail stays');
        void paneBefore;
    }

    // ── 7. went through unchecked: says so ───────────────────────────────
    {
        const btn = freshRefusal([SLOT, OP]);
        toasts.length = 0;
        window.PulsesPage.deleteTogether(btn);
        const note = 'Your lab code could not be run (no Python environment is selected): this edit was NOT checked against your gate. It was written unchecked.';
        answer(fetches[fetches.length - 1], 200, { ok: true, warning: note });
        await sleep(50);
        const w = d.querySelector('#inspector-pane .toast.toast-warning');
        ok(!!w && /NOT checked/.test(w.textContent), '7: the pane says the batch was written UNCHECKED');
        ok(!toasts.length, '7: said once, where the user looks (no second toast)');
        // the pane moved on: the warning still reaches the user, as a toast
        const btn2 = freshRefusal([SLOT, OP]);
        window.PulsesPage.deleteTogether(btn2);
        d.getElementById('inspector-pane').innerHTML = detailHtml('qubits.q1.xy.operations.x180');
        answer(fetches[fetches.length - 1], 200, { ok: true, warning: note });
        await sleep(50);
        ok(toasts.some((t) => t.level === 'warning' && /NOT checked/.test(t.msg)),
           '7: a pane that moved on gets the UNCHECKED warning as a toast');
    }

    // ── 8. a pane that moved on is left alone ────────────────────────────
    {
        const btn = freshRefusal([SLOT, OP]);
        triggers.length = 0;
        window.PulsesPage.deleteTogether(btn);
        d.getElementById('inspector-pane').innerHTML = detailHtml('qubits.q1.xy.operations.x180');
        answer(fetches[fetches.length - 1], 200, { ok: true });
        await sleep(50);
        ok(d.getElementById('pulse-detail-root').getAttribute('data-pulse-path') === 'qubits.q1.xy.operations.x180',
           '8: another pulse opened meanwhile keeps its pane');
        ok(triggers.indexOf('pulses-changed') >= 0, '8: the table still refreshes');
    }

    console.log(fails ? fails + ' FAILED' : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
