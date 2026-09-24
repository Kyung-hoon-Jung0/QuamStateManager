/* docs/124 C-1 — the Live-diff per-row ✓/✗ buttons must actually work.
 *
 * The tree renderer (one IIFE) wires the buttons to _acceptLiveValue /
 * _rejectLiveValue, which are locals of the live-diff IIFE. The bare calls
 * threw ReferenceError on EVERY click, so ✓ Accept was a silent no-op: the
 * user believed Qualibrate's value was staged, applied to live, and the value
 * they explicitly accepted was absent from what hit the hardware. Pre-existing
 * on main; zero coverage anywhere (a button.click() returning true never meant
 * the handler ran — the earlier probe was fooled by exactly that).
 *
 * Pins, at the OBSERVABLE level (the pre-fix behavior produces none of these):
 *   1. the handlers are exported (typeof function on window)
 *   2. clicking ✓ on a real rendered diff row issues the /field/edit-batch
 *      request with the row's dot_path, and the row turns pending
 *   3. clicking ✗ clears the incoming marker and updates the diff-bar count
 *   5. (QA r2-01) the Explorer overlay renders every pair the bar counts --
 *      an added key, a removed key, a shortened list -- as a row with ✓/✗
 *   6. (QA r2-02) ✓ / Accept all CREATE an added key and DELETE a removed
 *      one (never a value-less update = a stored null); a partial Accept all
 *      marks the applied rows in place, no stale re-diff
 *   7. (QA JT-03) the user's own unapplied edits are never announced as
 *      "Qualibrate changed", their ✓ says it discards, Accept all asks before
 *      reverting them, and the last reviewed row ends the diff
 *
 * Run: node tests/livediff_buttons_selfcheck.cjs
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
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
Object.defineProperty(global, 'navigator',
                      { value: window.navigator, configurable: true, writable: true });
global.location = window.location;
const STORE = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
[[global, 'localStorage'], [global, 'sessionStorage'],
 [window, 'localStorage'], [window, 'sessionStorage']].forEach(function (t) {
    Object.defineProperty(t[0], t[1], { value: STORE, configurable: true, writable: true });
});
// Recording fetch stub shaped like the real endpoints _liveFetchJson expects:
// Response-like with ok/status/text().
const fetches = [];
let liveDiffPayload = { live_state: {}, live_wiring: {} };
let liveDiffHold = null;         // a promise the live-diff read waits on (r2-02: an in-flight read)
let editBatchResponder = null;   // (opts) -> body, for the Accept-all pins
function fetchStub(url, opts) {
    fetches.push({ url: String(url), opts: opts || {} });
    const body = String(url).indexOf('/state/live-diff') === 0
        ? liveDiffPayload
        : (editBatchResponder && String(url).indexOf('/field/edit-batch') === 0)
            ? editBatchResponder(opts || {})
            : { ok: true, results: [{ ok: true }], tray_html: '' };
    const hold = (String(url).indexOf('/state/live-diff') === 0 && liveDiffHold) ? liveDiffHold : Promise.resolve();
    return hold.then(() => ({
        ok: true, status: 200,
        text: function () { return Promise.resolve(JSON.stringify(body)); },
    }));
}
global.fetch = fetchStub;
Object.defineProperty(window, 'fetch',
                      { value: fetchStub, configurable: true, writable: true });
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

// This harness runs app.js through Node-realm eval, where a window PROPERTY
// is not a bare-identifier global (in a browser it is — the global object IS
// window). explorerLiveDiff's ON branch calls renderJsonTree bare across
// IIFEs, which resolves fine in production and ReferenceErrors here unless
// bridged (the CLAUDE.md bridge-every-bare-global rule; the miss is swallowed
// by the diff path's own catch into a recover toast).
global.renderJsonTree = window.renderJsonTree;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const settle = () => new Promise((r) => setTimeout(r, 30));
let refreshes = 0;   // _softRefreshLiveSurface calls (a pane re-GET)

(async function main() {

// ── 1. the exports exist ───────────────────────────────────────────────────
ok(typeof window._acceptLiveValue === 'function',
   'window._acceptLiveValue is exported (cross-IIFE reachable)');
ok(typeof window._rejectLiveValue === 'function',
   'window._rejectLiveValue is exported (cross-IIFE reachable)');

// ── 2. a real rendered diff row: ✓ stages through /field/edit-batch ───────
const host = window.document.createElement('div');
host.id = 'explorer-tree-state';
window.document.body.appendChild(host);
const cnt = window.document.createElement('span');
cnt.id = 'livediff-bar-count';
cnt.textContent = '2';
window.document.body.appendChild(cnt);

// Working copy vs live: two leaves differ -> two rows carry ✓/✗. Children are
// built LAZILY (production renders at defaultDepth 1 then expands along the
// diff paths), so expand by clicking the real toggles — the same machinery a
// user drives — until no collapsed node remains.
window.renderJsonTree('explorer-tree-state',
    { qubits: { q1: { f_01: 4.30e9, T1: 30e-6 } } },
    { defaultDepth: 1,
      refData: { qubits: { q1: { f_01: 4.31e9, T1: 31e-6 } } },
      valueClick: 'livediff' });
for (let pass = 0; pass < 10; pass++) {
    const collapsed = host.querySelectorAll('.tree-toggle.collapsed');
    if (!collapsed.length) break;
    collapsed.forEach((t) => t.click());
    await settle();
}

const accBtns = host.querySelectorAll('.tree-accept-btn');
const rejBtns = host.querySelectorAll('.tree-reject-btn');
ok(accBtns.length === 2, 'both differing leaves render an accept button (got ' + accBtns.length + ')');
ok(rejBtns.length === 2, 'both differing leaves render a reject button (got ' + rejBtns.length + ')');
if (!accBtns.length || !rejBtns.length) { console.error(String(fails) + ' check(s) failed'); process.exit(1); }

// Capture the row BEFORE clicking: a successful accept runs _clearIncoming,
// which removes the ✓/✗ buttons themselves — the button is detached afterwards.
const accRow = accBtns[0].closest('.tree-row');
const before = fetches.length;
accBtns[0].click();
await settle();
ok(fetches.length === before + 1,
   'clicking accept issues exactly one request (pre-fix: ReferenceError, zero requests)');
const req = fetches[fetches.length - 1] || { url: '', opts: {} };
ok(req.url.indexOf('/field/edit-batch') === 0,
   'the request is /field/edit-batch (got ' + req.url + ')');
let body = null;
try { body = JSON.parse(req.opts.body); } catch (e) {}
ok(!!(body && body.updates && body.updates.length === 1 &&
      /qubits\.q1\./.test(body.updates[0].dot_path)),
   'the body carries the clicked row\'s dot_path (' +
   (body && body.updates && body.updates[0] && body.updates[0].dot_path) + ')');
ok(accRow && accRow.className.indexOf('tree-row-pending') >= 0,
   'the accepted row turns pending after the edit lands');
ok(!accBtns[0].isConnected,
   'the accepted row\'s buttons are removed (incoming markers cleared)');

// ── 3. ✗ clears the incoming marker and speaks to the bar count ───────────
const rejRow = rejBtns[1].parentElement;
const hadIncoming = rejRow.className.indexOf('tree-row-incoming') >= 0;
ok(hadIncoming, 'precondition: the reject row is marked incoming before the click');
rejBtns[1].click();
await settle();
ok(rejRow.className.indexOf('tree-row-incoming') < 0,
   'clicking reject clears the incoming marker (pre-fix: marker stayed)');
ok(cnt.textContent !== '2',
   'the diff-bar count is updated by the reject (was "2", now "' + cnt.textContent + '")');

// ── 4. docs/124 M-4/M-5 — diff-mode truth is the DOM, both halves together ──
// The old closure flag survived pane swaps while the toggle's class did not:
// a fresh render with diff previously ON produced flag=true/DOM=inactive and
// the FIRST click ran the OFF branch — a silent dead click. And the
// zero-pairs no-op flipped only the flag, leaving a stuck-lit toggle its own
// button could never turn off.
{
    const d = window.document;
    const wireHost = d.createElement('div');
    wireHost.id = 'explorer-tree-wiring';
    d.body.appendChild(wireHost);
    const toggle = d.createElement('button');
    toggle.id = 'explorer-livediff-toggle';
    d.body.appendChild(toggle);
    const bar = d.createElement('div');
    bar.id = 'explorer-livediff-bar';
    bar.hidden = true;
    bar.innerHTML = '<span id="livediff-bar-count"></span>';
    d.body.appendChild(bar);
    window._softRefreshLiveSurface = function () { refreshes++; };

    // fresh render (toggle INACTIVE — what _explorer.html always ships):
    // an argless call must derive ON from the DOM and fetch the diff. With
    // the old shadow flag stuck true, this exact call ran the OFF branch and
    // fetched NOTHING — the dead first click.
    const sHost = d.getElementById('explorer-tree-state');
    sHost._treeData = { qubits: { q1: { f_01: 1 } } };
    wireHost._treeData = { a: 1 };
    liveDiffPayload = { live_state: { qubits: { q1: { f_01: 2 } } },
                        live_wiring: { a: 1 } };
    window.showToast = function () {};   // capture-free stub; jsdom has no toast UI
    const before = fetches.length;
    window.explorerLiveDiff();
    await settle();
    const diffFetches = fetches.slice(before).filter(function (f) {
        return f.url.indexOf('/state/live-diff') === 0;
    });
    ok(diffFetches.length === 1,
       'M-4: with an inactive toggle, the FIRST argless call goes ON and fetches the diff');
    ok(toggle.classList.contains('active') && !bar.hidden,
       'M-4: and the toggle + bar arm together');

    // The search SURVIVES the diff rebuild, behaviorally (closes the
    // claim-audit pin gap: explorer_search_selfcheck pins this call site at
    // the source level only — gutting the helper's body kept it green). The
    // overlay render above rebuilt both trees; with a query armed, the
    // re-apply must actually filter the rebuilt rows.
    const box = d.createElement('input');
    box.id = 'explorer-search';
    box.className = 'tree-search';
    box.value = 'T1';
    d.body.appendChild(box);
    // _activeTreeId is defined by _explorer.html's inline fragment script
    // (line ~70), not by app.js — mirror its state-tab answer here.
    window._activeTreeId = function () { return 'explorer-tree-state'; };
    window.explorerSearch('T1');
    sHost._treeData = { qubits: { q1: { f_01: 1, T1: 30 } } };
    liveDiffPayload = { live_state: { qubits: { q1: { f_01: 2, T1: 30 } } },
                        live_wiring: { a: 1 } };
    window.explorerLiveDiff(true);
    await settle();
    ok(toggle.classList.contains('active'),
       're-apply precondition: the overlay armed with a query set');
    // jsonTreeSearch applies behind its own 200 ms debounce — assert after it.
    await new Promise((r) => setTimeout(r, 350));
    ok(sHost.querySelectorAll('.tree-search-hidden').length > 0,
       'the search is RE-APPLIED over the rebuilt tree (non-matching rows hidden), not just remembered');
    window.explorerSearch('');
    window.explorerLiveDiff(false);
    await settle();

    // stuck-lit + zero pairs: the ON path finding nothing must clear BOTH
    // halves — the old code cleared only the flag and the lit toggle lied.
    sHost._treeData = { qubits: { q1: { f_01: 2 } } };
    liveDiffPayload = { live_state: { qubits: { q1: { f_01: 2 } } },
                        live_wiring: { a: 1 } };
    window.explorerLiveDiff(true);
    await settle();
    ok(!toggle.classList.contains('active') && bar.hidden,
       'M-5: the zero-pairs branch clears the toggle AND the bar (no stuck-lit liar)');
}


// ── 5-7. QA r2-01 / r2-02 / JT-03 — the Explorer overlay, end to end ───────
{
    const d = window.document;
    const sHost = d.getElementById('explorer-tree-state');
    const wHost = d.getElementById('explorer-tree-wiring');
    const toggle = d.getElementById('explorer-livediff-toggle');
    const bar = d.getElementById('explorer-livediff-bar');
    // the REAL bar markup (ids the code fills), from the template
    const tpl = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates', '_explorer.html'), 'utf8');
    const bi = tpl.indexOf('<div id="explorer-livediff-bar"');
    const inner = tpl.slice(tpl.indexOf('>', bi) + 1, tpl.indexOf('<button', bi)).replace(/\{#[\s\S]*?#\}/g, '');
    bar.innerHTML = inner;
    cnt.remove();                          // section 2's stand-alone count would shadow the bar's id
    d.getElementById('explorer-search').value = '';   // section 4's query is not part of these pins
    const toasts = [];
    window.showToast = function (m) { toasts.push(String(m)); };
    let confirms = [];
    window.confirm = function (m) { confirms.push(String(m)); return window.__confirmAnswer; };
    const barText = () => bar.textContent.replace(/\s+/g, ' ').trim();
    const nodeAt = (host, p) => host.querySelector('.tree-node[data-path="' + p + '"]');
    const rowAt = (host, p) => { const n = nodeAt(host, p); return n && n.querySelector(':scope > .tree-row'); };
    const accOf = (host, p) => { const r = rowAt(host, p); return r && r.querySelector(':scope > .tree-accept-btn'); };
    const lastBatch = () => { const f = fetches.filter((x) => x.url.indexOf('/field/edit-batch') === 0).pop(); return f ? JSON.parse(f.opts.body) : null; };

    const WORK_S = { qubits: { q1: { T1: 1, chi: -350, extras: {}, gef: [1, 2, 3] }, q3: { T1: 5 } } };
    const LIVE_S = { qubits: { q1: { T1: 1, extras: { qa_added: 123.5 }, gef: [1, 2] }, q3: { T1: 6 } } };
    const WORK_W = { network: { cluster_name: 'G', host: 'h' } };
    const LIVE_W = { network: { host: 'h', qa_net: 'x' } };
    const PAIRS_S = ['qubits.q1.chi', 'qubits.q1.extras.qa_added', 'qubits.q1.gef', 'qubits.q3.T1'];
    const PAIRS_W = ['network.cluster_name', 'network.qa_net'];
    async function arm(attr) {
        window.explorerLiveDiff(false);
        await settle();
        sHost._treeData = JSON.parse(JSON.stringify(WORK_S));
        wHost._treeData = JSON.parse(JSON.stringify(WORK_W));
        liveDiffPayload = Object.assign({ ok: true, live_state: LIVE_S, live_wiring: LIVE_W }, attr);
        window.explorerLiveDiff(true);
        await settle();
    }

    // 5. every counted pair is a reviewable row
    await arm({ live_moved: true, mine: [], conflicts: [], external: PAIRS_S.concat(PAIRS_W) });
    ok(toggle.classList.contains('active') && /\b6\b/.test(d.getElementById('livediff-bar-count').textContent),
       'r2-01 fixture: the overlay is on and counts 6 pairs (' + barText() + ')');
    ok(!!nodeAt(sHost, 'qubits.q1.extras.qa_added') && !!nodeAt(wHost, 'network.qa_net'),
       'r2-01: a key only the live chip has is rendered as a row');
    const withBtn = PAIRS_S.filter((p) => accOf(sHost, p)).concat(PAIRS_W.filter((p) => accOf(wHost, p)));
    ok(withBtn.length === 6, 'r2-01: every counted pair has a ✓ row (' + withBtn.join(',') + ')');
    ok(sHost.querySelectorAll('.tree-accept-btn').length + wHost.querySelectorAll('.tree-accept-btn').length === 6,
       'r2-01: and no row outside the pairs gets a ✓ (rows == count)');
    const g2 = rowAt(sHost, 'qubits.q1.gef.2');
    ok(g2 && /removed/i.test(g2.textContent) && !g2.querySelector('.tree-accept-btn'),
       'r2-01: an element of the shortened list reads "removed" without a second ✓');
    ok(/\[2 items\]/.test(rowAt(sHost, 'qubits.q1.gef').textContent),
       'r2-01: the shortened list shows its live length');
    ok(/^Qualibrate changed 6 field\(s\)/.test(barText()),
       'JT-03: with only live-side changes the bar keeps "Qualibrate changed" (' + barText() + ')');

    // 6. per-row ✓ create / delete
    accOf(sHost, 'qubits.q1.extras.qa_added').click();
    await settle();
    let b = lastBatch();
    ok(b && b.updates.length === 1 && b.updates[0].dot_path === 'qubits.q1.extras.qa_added'
       && b.updates[0].value === 123.5 && b.updates[0].create === true,
       'r2-02: ✓ on an added key CREATES it (' + JSON.stringify(b) + ')');
    accOf(sHost, 'qubits.q1.chi').click();
    await settle();
    b = lastBatch();
    ok(b && b.updates.length === 1 && b.updates[0].dot_path === 'qubits.q1.chi'
       && b.updates[0].delete === true && !('value' in b.updates[0]),
       'r2-02: ✓ on a removed key DELETES it, never a value-less update (' + JSON.stringify(b) + ')');
    ok(/\b4\b/.test(d.getElementById('livediff-bar-count').textContent), 'r2-02: two ✓ took the count 6 -> 4');

    // 6b. Accept all: create/delete aware, then partial failure handled in place
    await arm({ live_moved: true, mine: [], conflicts: [], external: PAIRS_S.concat(PAIRS_W) });
    editBatchResponder = function (opts) {
        const u = JSON.parse(opts.body).updates;
        return { ok: false, tray_html: '', results: u.map((x) => x.dot_path === 'qubits.q3.T1'
            ? { dot_path: x.dot_path, applied: false, error: 'nope' }
            : { dot_path: x.dot_path, applied: true, display: '' }) };
    };
    const liveReads = () => fetches.filter((x) => x.url.indexOf('/state/live-diff') === 0).length;
    const r0 = liveReads();
    window.explorerAcceptAll();
    await settle();
    b = lastBatch();
    const ups = (b && b.updates) || [];
    ok(ups.length === 6 && ups.every((u) => u.delete === true || 'value' in u),
       'r2-02: Accept all never sends an update without a value unless it is a delete (' + JSON.stringify(ups) + ')');
    ok(ups.filter((u) => u.delete === true).map((u) => u.dot_path).sort().join() === 'network.cluster_name,qubits.q1.chi'
       && ups.filter((u) => u.create === true).map((u) => u.dot_path).sort().join() === 'network.qa_net,qubits.q1.extras.qa_added',
       'r2-02: removals are deletes and additions are creates');
    await settle();
    ok(liveReads() === r0, 'r2-02: a partial Accept all does not re-read the diff against stale tree data');
    ok(/\b1\b/.test(d.getElementById('livediff-bar-count').textContent) && toggle.classList.contains('active'),
       'r2-02: the count drops by what applied (' + d.getElementById('livediff-bar-count').textContent + ')');
    ok(!rowAt(sHost, 'qubits.q1.chi').classList.contains('tree-row-incoming')
       && rowAt(sHost, 'qubits.q3.T1').classList.contains('tree-row-incoming'),
       'r2-02: applied rows are cleared in place; the rejected row stays marked');
    editBatchResponder = null;

    // 7. JT-03: the user's own edits
    await arm({ live_moved: false, mine: [], conflicts: [], external: [], unaccounted: 'saved edits' });
    ok(!/Qualibrate changed/.test(barText()) && /your own unapplied values/.test(barText()),
       'JT-03: live unchanged since sync -> the bar says these are the user\'s own values (' + barText() + ')');
    const mr = rowAt(sHost, 'qubits.q3.T1');
    ok(mr.classList.contains('tree-row-mine') && /Discard your edit/.test(mr.querySelector('.tree-accept-btn').title),
       'JT-03: an own-edit row is marked and its ✓ says it discards the edit');
    window.__confirmAnswer = false; confirms = [];
    const nb = fetches.length;
    window.explorerAcceptAll();
    await settle();
    ok(confirms.length === 1 && /All 6 differing fields are your own/.test(confirms[0]) && fetches.length === nb,
       'JT-03: Accept all over own edits asks first, and Cancel sends nothing (' + confirms[0] + ')');
    // mixed: q3.T1 is the user's own edit, the rest moved on the live chip
    await arm({ live_moved: true, mine: ['qubits.q3.T1'], conflicts: [], external: PAIRS_S.slice(0, 3).concat(PAIRS_W) });
    ok(/5 changed on the live chip/.test(barText()) && /1 is your own unapplied edit/.test(barText()) && !/^Qualibrate changed/.test(barText()),
       'JT-03: a mixed diff names both parts (' + barText() + ')');
    window.__confirmAnswer = false; confirms = [];
    editBatchResponder = (opts) => ({ ok: true, tray_html: '', results: JSON.parse(opts.body).updates.map((x) => ({ dot_path: x.dot_path, applied: true, display: '' })) });
    window.explorerAcceptAll();
    await settle();
    editBatchResponder = null;
    b = lastBatch();
    ok(confirms.length === 1 && b && b.updates.length === 5 && !b.updates.some((u) => u.dot_path === 'qubits.q3.T1'),
       'JT-03: Cancel accepts only the live-side rows; the own edit is left out (' + (b && b.updates.map((u) => u.dot_path).join()) + ')');
    // the last reviewed row ends the diff
    ok(toggle.classList.contains('active'), 'JT-03 precondition: one own row is left, diff still on');
    const refreshesBefore = refreshes;
    rowAt(sHost, 'qubits.q3.T1').querySelector('.tree-reject-btn').click();
    await settle();
    ok(!toggle.classList.contains('active') && bar.hidden,
       'JT-03: reviewing the last row ends the diff (no "changed 0 field(s)" bar)');
    // ...IN PLACE (review): the reviewed rows stay as they are, the pane is not re-fetched
    ok(refreshes === refreshesBefore,
       'JT-03: the last review does not re-GET the pane (' + (refreshes - refreshesBefore) + ' soft refresh)');
    ok(!!rowAt(sHost, 'qubits.q1.gef') && rowAt(sHost, 'qubits.q1.gef').classList.contains('tree-row-pending'),
       'JT-03: the accepted rows are still on screen, marked pending');
    // ...and the accepts patched the MODEL, so the next diff does not re-count them
    const m = sHost._treeData, mw = wHost._treeData;
    ok(m.qubits.q1.chi === undefined && m.qubits.q1.extras.qa_added === 123.5 && JSON.stringify(m.qubits.q1.gef) === '[1,2]'
       && mw.network.cluster_name === undefined && mw.network.qa_net === 'x',
       'JT-03: delete / create / replace accepts reached the tree model (' + JSON.stringify(m.qubits.q1) + ' ' + JSON.stringify(mw) + ')');
    liveDiffPayload = { ok: true, live_state: LIVE_S, live_wiring: LIVE_W, live_moved: true, mine: [], conflicts: [], external: ['qubits.q3.T1'] };
    window.explorerLiveDiff(true);
    await settle();
    ok(toggle.classList.contains('active') && d.getElementById('livediff-bar-count').textContent === '1',
       'JT-03: the next diff counts only the row that was kept (' + d.getElementById('livediff-bar-count').textContent + ')');

    // r2-02 (review): the toggle is busy while the live read is in flight; a second press is a no-op
    window.explorerLiveDiff(false);
    await settle();
    let release = null;
    liveDiffHold = new Promise((r) => { release = r; });
    const nf = fetches.filter((f) => f.url.indexOf('/state/live-diff') === 0).length;
    window.explorerLiveDiff(true);
    await settle();
    ok(toggle.disabled && toggle.getAttribute('aria-busy') === 'true',
       'r2-02: the toggle is busy while the read is in flight');
    window.explorerLiveDiff();          // the impatient second press (argless, as the button sends it)
    window.explorerLiveDiff(true);
    await settle();
    const nf2 = fetches.filter((f) => f.url.indexOf('/state/live-diff') === 0).length;
    ok(nf2 === nf + 1, 'r2-02: a second press during the read starts no second read and turns nothing off (' + (nf2 - nf) + ' reads)');
    release();
    await settle(); await settle();
    liveDiffHold = null;
    ok(!toggle.disabled && !toggle.hasAttribute('aria-busy') && toggle.classList.contains('active'),
       'r2-02: the read answers, the toggle is live again and ON');

    // r2-01 (review): ONE builder -- the renderer's leaf rows call the overlay's _ldReviewButtons
    const realBuilder = window._ldReviewButtons;
    ok(typeof realBuilder === 'function', 'r2-01: the review-button builder is exported');
    const built = [];
    window._ldReviewButtons = function (row, p, node) { built.push(p.dot_path + ':' + (p.op || 'leaf')); return realBuilder(row, p, node); };
    await arm({ live_moved: true, mine: [], conflicts: [], external: PAIRS_S.concat(PAIRS_W) });
    window._ldReviewButtons = realBuilder;
    ok(built.indexOf('qubits.q3.T1:leaf') >= 0,
       'r2-01: a leaf row gets its buttons from the one builder (' + built.join(' ') + ')');
    const tmp = d.createElement('div');
    realBuilder(tmp, { dot_path: 'x', value: 1 });
    ok(accOf(sHost, 'qubits.q3.T1').title === tmp.querySelector('.tree-accept-btn').title
       && rowAt(sHost, 'qubits.q3.T1').querySelector('.tree-reject-btn').title === tmp.querySelector('.tree-reject-btn').title,
       'r2-01: the leaf row\'s titles are the builder\'s, byte for byte');
    ok(/only the live chip has it/.test(accOf(sHost, 'qubits.q1.extras.qa_added').title)
       && /the live chip does not have it/.test(accOf(sHost, 'qubits.q1.chi').title),
       'r2-01: the structural rows keep their create / delete titles');

    // JT-03 (review): dismissing a structural row leaves no residue in the kept pane
    rowAt(sHost, 'qubits.q1.extras.qa_added').querySelector('.tree-reject-btn').click();
    await settle();
    ok(!nodeAt(sHost, 'qubits.q1.extras.qa_added'),
       'JT-03: a dismissed live-only key leaves the tree (the working state has no such row)');
    rowAt(sHost, 'qubits.q1.chi').querySelector('.tree-reject-btn').click();
    await settle();
    ok(!!nodeAt(sHost, 'qubits.q1.chi') && !rowAt(sHost, 'qubits.q1.chi').querySelector('.tree-sidetag'),
       'JT-03: a kept key drops its "removed" tag');
    window.explorerLiveDiff(false);
    await settle();

    // latent: a failed live read must not break the toggle
    liveDiffPayload = { ok: false, error: 'unreadable' };
    window.explorerLiveDiff(true);
    await settle();
    let threw = null;
    try { window.explorerLiveDiff(); } catch (e) { threw = e; }
    await settle();
    ok(!threw, 'after a failed live read the argless toggle still works (' + (threw && threw.message) + ')');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
process.exit(0);
})().catch(function (e) {
    console.error('FAIL: selfcheck threw: ' + (e && e.stack || e));
    process.exit(1);
});
