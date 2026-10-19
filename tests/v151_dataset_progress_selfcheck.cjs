/* R3B MINOR 8 -- the REAL dataset-virtual.js under jsdom: the "wrote state"
 * mark says what the run did (no promise that its fits can be applied), and
 * the delta polls' `reindex` progress ("Re-indexing after upgrade: N of M
 * runs") shows, advances, and never lingers once the rebuild is done.
 *
 * Run: node tests/v151_dataset_progress_selfcheck.cjs
 * (driven by tests/test_v151_r5b_dataset_progress.py)
 */
'use strict';
const fs = require('fs'), path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }
const rows = [{ id: 1, exp: 'scan', date: '2026-10-18', time: '01:00:00',
    q: ['q1'], p: [], oc: {}, bm: false, tags: [], status: 'finished',
    dur: 1, note: '', hs: false, sm: {}, pm: {}, ws: 2, f: 'f1' }];
const dom = new JSDOM('<body><div id="table-pane"><div class="datasets-page"><h2 class="ds-head-title">Runs</h2>' +
    '<script id="ds-rows-data" type="application/json" data-now="1000">' + JSON.stringify(rows) + '</script>' +
    '<div id="datasets-scroll"><table><thead id="datasets-thead"></thead><tbody id="datasets-tbody"></tbody></table></div>' +
    '</div></div></body>', { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
const w = dom.window, d = w.document;
let answer, calls = 0, checks = 0;
function ok(c, msg) { checks++; if (!c) throw new Error('FAIL: ' + msg); console.log('ok - ' + msg); }
w.fetch = url => {
    if (!String(url).startsWith('/datasets/changes-since')) return new Promise(() => {});
    calls++;
    return Promise.resolve({ ok: true, json: async () => answer });
};
w.htmx = { ajax: () => Promise.resolve(), process() {}, trigger() {} };
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/dataset-virtual.js'), 'utf8'));
w.DatasetVirtual.init();
const tick = () => new Promise(r => setTimeout(r, 50));
async function poll(body) {
    answer = { updated: [], vanished: [], now: 1000 + calls, ...body };
    w.DatasetVirtual.pollNow(); await tick();
}
(async () => {
    await tick();
    const mark = d.querySelector('.ds-wrote');
    ok(mark && mark.title === 'This run wrote 2 values into the chip state', 'the mark reports the state write only');
    ok(!/fit|appl/i.test(mark.title), 'the mark promises no click-to-apply capability');
    await poll({ partial: true, reindex: { done: 2, total: 16 } });
    const note = d.getElementById('ds-scan-note');
    ok(note && !note.hidden && note.textContent === 'Re-indexing after upgrade: 2 of 16 runs',
       'an upgrade poll shows honest progress even if the initial note was absent');
    await poll({ partial: true, reindex: { done: 8, total: 16 } });
    ok(note.textContent.includes('8 of 16'), 'the next poll advances the progress');
    await poll({ partial: true });
    ok(!note.hidden && /still indexing runs/.test(note.textContent) && !/of 16/.test(note.textContent),
       'another partial scan does not leave stale upgrade counts: ' + note.textContent);
    await poll({ partial: false });
    ok(note.hidden, 'the note clears when indexing completes');
    console.log('DONE (' + checks + ' assertions)');
    w.close();
})().catch(e => { console.error(e.stack); w.close(); process.exit(1); });
