'use strict';
// v1.5.1 (r6, D4a P2-5): the CZ gate plan table under the shipped cz-plan.js. A table wider than
// its wrap scrolls inside it with pair AND operation held in view: the operation's sticky offset
// is the pair column's drawn width, and follows it when that width changes (a UI-scale change
// moves the padding, so the border box is observed). The held columns get an edge only while
// other columns pass under them. The Last set column keeps a date with its time and the
// "newer" badge whole, with the text itself unchanged.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const OP = 'qubit_pairs.p.macros.a', OTHER = 'qubit_pairs.p.macros.b';
const page = `<div id="czp-root" class="czp-root" data-selected="">
<div class="czp-table-wrap"><table id="czp-table"><thead><tr><th class="czp-stick">Pair</th><th class="czp-stick czp-stick-op">Operation</th><th>Last set</th></tr></thead><tbody>
<tr class="czp-row" data-op="${OP}"><td class="czp-stick">p</td><td class="czp-stick czp-stick-op">a</td><td class="czp-last" data-last="${OP}">...</td></tr>
<tr class="czp-row" data-op="${OTHER}"><td class="czp-stick">p</td><td class="czp-stick czp-stick-op">b</td><td class="czp-last" data-last="${OTHER}">...</td></tr>
</tbody></table></div><p id="czp-history-note"></p></div>`;
const dom = new JSDOM('<!doctype html><body>' + page + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
Object.assign(w, {console});
const observers = [];
w.ResizeObserver = class { constructor(cb) { this.cb = cb; observers.push(this); } observe(el, opts) { this.el = el; this.opts = opts; } disconnect() {} };
let pairWidth = 57.5;
const pairTh = d.querySelector('th.czp-stick');
pairTh.getBoundingClientRect = () => ({width: pairWidth, height: 20, left: 0, top: 0, right: pairWidth, bottom: 20});
w.fetch = (url) => {
    if (url.startsWith('/cz-plan/history')) return Promise.resolve({json: () => Promise.resolve({ok: true, mode: 'ledger', note: null, ops: {
        [OP]: {last_set: {t: '2026-01-02T03:04:05Z', run_id: 9, provenance: 'run_saved'}, warnings: [{text: 'older'}, {text: 'older too'}]},
        [OTHER]: {last_set: {t: '2026-01-02T03:04:05Z', run_id: 7, provenance: 'run_proven'}, warnings: []}}})});
    return new Promise(() => {});
};
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
const wait = ms => new Promise(r => setTimeout(r, ms));
w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));
(async () => {
    w.CzPlan.mount(); await wait(30);
    const table = d.getElementById('czp-table');
    check(table.style.getPropertyValue('--czp-stick-op') === '57.5px', 'the operation sticks right of the drawn pair column: ' + table.style.getPropertyValue('--czp-stick-op'));
    const ro = observers.find(o => o.el === pairTh);
    check(ro && ro.opts && ro.opts.box === 'border-box', 'the pair column is watched by its border box (padding follows the UI scale)');
    pairWidth = 71.25; ro.cb([]);
    check(table.style.getPropertyValue('--czp-stick-op') === '71.25px', 'a wider pair column moves the operation with it');
    const wrap = d.querySelector('.czp-table-wrap');
    check(!wrap.classList.contains('czp-scrolled-x'), 'no edge while nothing passes under the held columns');
    wrap.scrollLeft = 40; wrap.dispatchEvent(new w.Event('scroll'));
    check(wrap.classList.contains('czp-scrolled-x'), 'scrolled sideways: the held columns get their edge');
    wrap.scrollLeft = 0; wrap.dispatchEvent(new w.Event('scroll'));
    check(!wrap.classList.contains('czp-scrolled-x'), 'back at the start: the edge goes');
    const saved = d.querySelector(`td[data-last="${OP}"]`), proven = d.querySelector(`td[data-last="${OTHER}"]`);
    check(saved.textContent === '2026-01-02', 'UX-6: the visible date is compact: ' + saved.textContent);
    const stamps = Array.from(saved.querySelectorAll('.czp-nowrap')).map(s => s.textContent);
    check(stamps.includes('2026-01-02') && saved.querySelector('span').title.includes('2026-01-02 03:04:05'), 'date stays whole; time remains on hover');
    check(saved.querySelector('span').title.includes('2 newer'), 'the newer count remains on hover');
    check(saved.querySelector('span').getAttribute('title') !== null, 'the saved-in hint is still the first span');
    check(proven.querySelector('.czp-nowrap') && proven.querySelector('.czp-nowrap').textContent === '2026-01-02' &&
          proven.querySelector('span').title.includes('run #7'), 'a proven writer: same stamp, same text');
    console.log('cz table fit: all passed (' + checks + ' checks)');
})().catch(e => { console.error(e); process.exit(1); });
