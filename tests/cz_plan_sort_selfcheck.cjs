'use strict';
// CZ gate plan table: it follows the map's Operation picker (rows of other operations hidden)
// and sorts by a clicked header, numbers numerically, rows without a value last.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(process.argv[2], 'utf8');
let checks = 0;
function check(v, m) { checks++; assert.ok(v, m); }
const wait = ms => new Promise(r => setTimeout(r, ms));
(async () => {
    const dom = new JSDOM('<!doctype html><body>' + html + '</body>', {url: 'http://localhost/cz-plan', runScripts: 'outside-only', pretendToBeVisual: true});
    const w = dom.window, d = w.document;
    const asked = [];
    w.fetch = url => { asked.push(url); return Promise.resolve({json: () => Promise.resolve({ok: false, error: 'stub'}), text: () => Promise.resolve('')}); };
    w.requirePlotly = () => Promise.resolve();
    ['topo-graph.js', 'cz-map.js', 'cz-plan.js'].forEach(f => w.eval(fs.readFileSync(path.join(root, 'quam_state_manager/web/static', f), 'utf8')));
    await wait(20);
    const r = d.getElementById('czp-root');
    const rows = () => Array.from(d.querySelectorAll('.czp-row'));
    const shown = () => rows().filter(tr => !tr.hidden).map(tr => tr.getAttribute('data-op'));
    const note = d.getElementById('czp-filter-note');
    check(shown().length === 4 && note.hidden, 'all operations listed while the map draws cz per pair');
    // the map picker is the trigger: a change redraws the map, which tells the table
    const sel = d.getElementById('czp-map-op');
    ['cz', 'cz_two'].forEach(n => { const o = d.createElement('option'); o.value = n; o.textContent = n; sel.appendChild(o); });
    sel.value = 'cz'; sel.dispatchEvent(new w.Event('change'));
    await wait(20);
    check(JSON.stringify(shown()) === JSON.stringify(['qubit_pairs.q1-2.macros.cz', 'qubit_pairs.q3-2.macros.cz']),
          'picking cz on the map lists only the cz rows: ' + shown());
    check(!note.hidden && note.textContent.includes('Showing 2 of 4'), 'the note says how many rows are listed');
    sel.value = 'cz_two'; sel.dispatchEvent(new w.Event('change'));
    await wait(20);
    check(JSON.stringify(shown()) === JSON.stringify(['qubit_pairs.q1-2.macros.cz_two']), 'cz_two lists one row');
    check(r.getAttribute('data-selected') === 'qubit_pairs.q1-2.macros.cz_two', 'a hidden selection moves to the first listed row');
    sel.value = ''; sel.dispatchEvent(new w.Event('change'));
    await wait(20);
    check(shown().length === 4 && note.hidden, 'cz per pair lists every row again');
    // sorting
    const th = k => d.querySelector('th[data-sort-key="' + k + '"] button');
    const order = () => rows().map(tr => tr.getAttribute('data-op'));
    const vals = k => rows().map(tr => tr.getAttribute('data-s-' + k));
    th('delta').click();
    let v = vals('delta'), nums = v.filter(x => x !== '').map(Number);
    check(nums.length >= 2 && nums.every((x, i) => !i || nums[i - 1] <= x), 'delta ascending: ' + v);
    check(v.indexOf('') < 0 || v.slice(v.indexOf('')).every(x => x === ''), 'rows without a value stay last');
    check(th('delta').parentElement.getAttribute('aria-sort') === 'ascending', 'aria-sort says ascending');
    th('delta').click();
    v = vals('delta'); nums = v.filter(x => x !== '').map(Number);
    check(nums.every((x, i) => !i || nums[i - 1] >= x) && th('delta').parentElement.getAttribute('aria-sort') === 'descending', 'again: descending ' + v);
    th('freq').click();
    v = vals('freq'); nums = v.filter(x => x !== '').map(Number);
    check(nums.length >= 2 && nums.every((x, i) => !i || nums[i - 1] <= x), 'operating frequency ascending: ' + v);
    check(th('delta').parentElement.getAttribute('aria-sort') === 'none', 'one sorted column at a time');
    // numbers compare as numbers, not text ("10" after "9")
    rows().forEach((tr, i) => tr.setAttribute('data-s-irb', ['10', '9', '', '0.5'][i]));
    th('irb').click();
    check(JSON.stringify(vals('irb')) === JSON.stringify(['0.5', '9', '10', '']), 'numeric order: ' + vals('irb'));
    const before = asked.length;
    th('pair').click();
    check(asked.length === before && order()[0].includes('q1-2'), 'a header click sorts and loads nothing');
    console.log(checks + ' assertions passed');
    dom.window.close();
})().catch(e => { console.error(e); process.exitCode = 1; });
