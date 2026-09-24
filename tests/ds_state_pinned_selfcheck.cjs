/* QA r2-08 — Pin & Browse: each column's State tab acts on ITS OWN run.
 *
 * The State tab's inline script assigned window.switchDatasetStateTab /
 * window._dsStateActiveId over the bare ids #ds-state-file-tabs,
 * ds-state-tree-* and ds-state-search. In the split both columns' scripts
 * run (pinned first, current last), the pinned clone's ids are
 * "pinned-"-prefixed, and the last definition wins -- so the pinned column's
 * wiring.json / node.json tabs and its tree search acted on the CURRENT
 * column, and the pinned script's first fetch rendered the PINNED run's
 * state.json into the current column's tree (whichever response came last).
 *
 * Driven on the markup the REAL template renders (argv[2] = JSON {<run>: html,
 * uids: [pinnedUid, currentUid]}) through the REAL app.js: the real
 * togglePinDataset clone + the real pinned beforeSwap interceptor, whose
 * _activatePinnedPane re-creates both columns' <script>s in DOM order.
 *
 * Run: node tests/ds_state_pinned_selfcheck.cjs <details.json>
 *      (driven by tests/test_ds_state_pinned.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const details = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const [uidP, uidC] = details.uids;              // pinned 4112, then browse to 4111
const htmlP = details['4112'], htmlC = details['4111'];

const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/datasets', runScripts: 'dangerously', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

/* JSON-file fetches are held until the test answers them, in the order it picks */
const held = [];          // {url, resolve}
window.fetch = function (url) {
    url = String(url);
    if (/\/dataset\/.+\/json\?file=/.test(url)) {
        return new Promise(function (resolve) { held.push({ url: url, resolve: resolve }); });
    }
    return new Promise(function () {});
};
function answer(uid, file) {
    const want = '/dataset/' + uid + '/json?file=' + file;
    const i = held.findIndex(function (h) { return h.url === want; });
    if (i < 0) return false;
    const h = held.splice(i, 1)[0];
    h.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ uid: uid, file: file }); } });
    return true;
}
window.htmx = { ajax: function () { return Promise.resolve(); }, on: function () {},
                trigger: function () {}, process: function () {} };
window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

// record what each tree container ends up showing, and what the toolbar targets
window.renderJsonTree = function (cid, data) {
    const el = document.getElementById(cid);
    if (el) el.setAttribute('data-shows', data && data.uid ? data.uid + ':' + data.file : '?');
};
const searches = [], depths = [];
window.jsonTreeSearch = function (id, q) { searches.push([id, q]); };
window.jsonTreeExpandAll = function (id) { depths.push(id); };
window.jsonTreeCollapseAll = function (id) { depths.push(id); };
window.jsonTreeExpandToDepth = function (id) { depths.push(id); };

(async function main() {
    const pane = document.getElementById('inspector-pane');
    // 1. open run P in the inspector (scripts run as an htmx swap runs them)
    pane.innerHTML = htmlP;
    window._activatePinnedPane(pane);
    ok(answer(uidP, 'state'), 'a fresh run loads its own state.json');
    await sleep(10);
    // 2. pin it, then browse to run C -> the real interceptor builds the split
    window.togglePinDataset();
    const ev = new window.CustomEvent('htmx:beforeSwap', { bubbles: true, cancelable: true,
        detail: { target: pane, shouldSwap: true, serverResponse: htmlC } });
    pane.dispatchEvent(ev);
    ok(!!pane.querySelector('.inspector-pinned-col #pinned-ds-tab-state')
       && !!pane.querySelector('.inspector-current-col #ds-tab-state'), 'split built: both columns carry a State tab');
    // the PINNED column's fetch answers LAST (the race the old code lost)
    ok(held.some(function (h) { return h.url === '/dataset/' + uidP + '/json?file=state'; }),
       'the pinned column re-renders its own run\'s state.json');
    answer(uidC, 'state');
    await sleep(5);
    answer(uidP, 'state');
    await sleep(10);
    const Pcol = pane.querySelector('.inspector-pinned-col'), Ccol = pane.querySelector('.inspector-current-col');
    ok(document.getElementById('ds-state-tree-state').getAttribute('data-shows') === uidC + ':state',
       'the current column shows the CURRENT run\'s state.json even when the pinned fetch lands last');
    ok(document.getElementById('pinned-ds-state-tree-state').getAttribute('data-shows') === uidP + ':state',
       'the pinned column shows the PINNED run\'s state.json');

    // 3. pinned column: click wiring.json
    const tabOf = function (col, name) {
        return Array.prototype.find.call(col.querySelectorAll('.tree-file-tab'),
            function (t) { return t.textContent.trim() === name; });
    };
    tabOf(Pcol, 'wiring.json').click();
    ok(tabOf(Pcol, 'wiring.json').classList.contains('active') && !tabOf(Pcol, 'state.json').classList.contains('active'),
       'pinned wiring.json tab becomes active in the PINNED column');
    ok(document.getElementById('pinned-ds-state-tree-wiring').style.display === ''
       && document.getElementById('pinned-ds-state-tree-state').style.display === 'none',
       'the pinned column shows its wiring tree');
    ok(tabOf(Ccol, 'state.json').classList.contains('active')
       && document.getElementById('ds-state-tree-wiring').style.display === 'none',
       'the current column did not move');
    ok(answer(uidP, 'wiring'), 'the pinned wiring.json is fetched for the PINNED run');
    await sleep(10);
    ok(document.getElementById('pinned-ds-state-tree-wiring').getAttribute('data-shows') === uidP + ':wiring',
       'the pinned wiring tree renders the pinned run\'s wiring.json');

    // 4. pinned search + depth button target the pinned tree
    const s = Pcol.querySelector('input.tree-search');
    s.value = 'run_end';
    s.dispatchEvent(new window.Event('input', { bubbles: true }));
    ok(searches.length === 1 && searches[0][0] === 'pinned-ds-state-tree-wiring' && searches[0][1] === 'run_end',
       'typing in the pinned search filters the PINNED tree');
    Array.prototype.find.call(Pcol.querySelectorAll('.tree-depth-group button'),
        function (b) { return b.textContent.trim() === 'All'; }).click();
    ok(depths[depths.length - 1] === 'pinned-ds-state-tree-wiring', 'the pinned depth buttons expand the PINNED tree');

    // 5. switching the pinned tab re-applies ITS search to the newly shown tree
    tabOf(Pcol, 'node.json').click();
    ok(searches[searches.length - 1][0] === 'pinned-ds-state-tree-node',
       'switching the pinned file re-runs the pinned search on the pinned node tree');

    // 6. the current column's own controls act on the current column
    tabOf(Ccol, 'data.json').click();
    ok(tabOf(Ccol, 'data.json').classList.contains('active') && tabOf(Pcol, 'node.json').classList.contains('active'),
       'current data.json tab switches only the current column');
    ok(answer(uidC, 'data'), 'the current data.json is fetched for the CURRENT run');

    // 7. no element (the sticky restore's call) means the browsed column
    window.switchDatasetStateTab('wiring');
    ok(tabOf(Ccol, 'wiring.json').classList.contains('active') && tabOf(Pcol, 'node.json').classList.contains('active'),
       'switchDatasetStateTab(name) with no element acts on the browsed (current) column');
    ok(window._dsStateActiveId() === 'ds-state-tree-wiring', '_dsStateActiveId() with no element names the current tree');

    if (fails) { console.error(fails + ' FAILED'); process.exit(1); }
    console.log('all ok');
    process.exit(0);
})().catch(function (e) { console.error('CRASH ' + (e && e.stack || e)); process.exit(1); });
