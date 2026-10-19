/* v1.5.1 (SB-P2-6 leftover): a Datasets page left open while one of its data
 * folders is moved away drops that folder's runs and names the folder.
 *
 * The sidebar learned to mark a gone folder "not found"; an open Datasets
 * table kept listing the folder's cached runs (the delta poll reported
 * nothing for it). The driver (tests/test_v151_p2_ds_gone_folder.py) renders
 * the REAL /datasets fragment with both folders present, then moves one and
 * asks the REAL /datasets/changes-since; the SHIPPED dataset-virtual.js
 * merges that answer here.
 *
 * R3B M5 (with ``back_delta`` + ``resend_delta`` in the fixture): the folder
 * comes back -- the REAL next answer no longer names it, so the note goes and
 * the next poll asks for its rows again (``resend=<key>``); the REAL answer to
 * that brings the run back into the table, and the poll after it stops asking.
 *
 * Run: node tests/v151_ds_gone_folder_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'dataset-virtual.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 40));

(async function () {
    const markup = FX.page.replace(/<script\b(?![^>]*application\/json)[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' + markup + '</div></body></html>',
        { url: 'http://localhost/datasets', pretendToBeVisual: true, runScripts: 'outside-only' });
    const w = dom.window, d = w.document;
    let served = 0;
    const answers = [FX.delta].concat(FX.back_delta ? [FX.back_delta, FX.resend_delta] : []);
    const polls = [];
    w.fetch = (url) => {
        if (String(url).indexOf('/datasets/changes-since') >= 0 && served < answers.length) {
            polls.push(String(url));
            const a = answers[served++];
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(a) });
        }
        if (String(url).indexOf('/datasets/changes-since') >= 0) polls.push(String(url));
        return new Promise(() => {});
    };
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {} };
    w.eval(SRC);
    w.DatasetVirtual.init();
    await tick();
    const exps = () => Array.from(d.querySelectorAll('#datasets-tbody tr[data-id]')).map((tr) => tr.getAttribute('data-exp'));
    ok(exps().indexOf(FX.gone_exp) >= 0, 'the page opens with the folder\'s run: ' + exps());
    const note = d.getElementById('ds-gone-note');
    ok(!!note && note.hidden, 'the real page carries the (hidden) note');
    d.dispatchEvent(new w.Event('visibilitychange'));
    await tick(120);
    ok(served === 1, 'the delta poll asked');
    ok(exps().indexOf(FX.gone_exp) < 0 && exps().indexOf(FX.kept_exp) >= 0,
       'the gone folder\'s run leaves the table, the other stays: ' + exps());
    ok(note && !note.hidden && note.textContent.indexOf('Data folder not found: ' + FX.gone_path) === 0,
       'and the note names the folder: ' + (note && note.textContent));
    if (FX.back_delta) {
        const poll = async () => { d.dispatchEvent(new w.Event('visibilitychange')); await tick(120); };
        await poll();
        ok(served === 2, 'the next poll asked');
        ok(polls[1].indexOf('resend=') < 0, 'it asked no resend yet: ' + polls[1]);
        ok(note.hidden && !note.querySelector('.ds-gone-one'), 'the folder is back: the note is cleared');
        await poll();
        ok(served === 3, 'the poll after asked');
        ok(polls[2].indexOf('resend=' + encodeURIComponent(FX.gone_key)) >= 0,
           'and asked for the folder\'s rows again: ' + polls[2]);
        ok(exps().indexOf(FX.gone_exp) >= 0 && exps().indexOf(FX.kept_exp) >= 0,
           'the folder\'s run is back in the table: ' + exps());
        await poll();
        ok(polls.length === 4 && polls[3].indexOf('resend=') < 0, 'and the next poll stops asking: ' + polls[3]);
    }
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
