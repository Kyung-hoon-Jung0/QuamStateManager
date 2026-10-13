'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/dataset-virtual.js'), 'utf8');
const row = {id: 1, f: 'root1', exp: 'scan', date: '2026-08-01', time: '01:00:00', q: ['q1'], tags: [], sm: {}, pm: {}};
function boot(storage, preset = '') {
    const dom = new JSDOM('<body><input id="dataset-search" data-preset="' + preset + '" value="' + preset + '">' +
        '<script id="ds-rows-data" data-now="1">' + JSON.stringify([row]) + '</script>' +
        '<div id="datasets-scroll"><table><tbody id="datasets-tbody"></tbody></table></div></body>', {
        url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true
    });
    const w = dom.window;
    if (storage) Object.defineProperty(w, 'sessionStorage', {value: storage});
    w.fetch = () => new Promise(() => {});
    w.eval(source);
    w.DatasetVirtual.init();
    return w;
}
const data = new Map();
const storage = {getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value)};
let checks = 0;
function check(value, message) { checks++; assert.ok(value, message); }
const first = boot(storage);
let input = first.document.getElementById('dataset-search');
input.value = 'exp:scan';
input.dispatchEvent(new first.Event('input', {bubbles: true}));
first.close();
const reload = boot(storage);
check(reload.document.getElementById('dataset-search').value === 'exp:scan', 'reload restores the filter from this tab');
const fresh = boot();
check(fresh.document.getElementById('dataset-search').value === '', 'another tab starts clean');
fresh.close();
const deepLink = boot(storage, 'q1');
check(deepLink.document.getElementById('dataset-search').value === 'q1', 'explicit deep link overrides the saved filter');
deepLink.close();
reload.clearDatasetFilters();
reload.close();
const cleared = boot(storage);
check(cleared.document.getElementById('dataset-search').value === '', 'Clear all persists the empty filter');
cleared.close();
const blocked = boot({getItem: () => {throw Error('blocked');}, setItem: () => {throw Error('blocked');}});
check(blocked.DatasetVirtual.rowCount() === 1, 'unavailable storage does not break the table');
blocked.close();
console.log(checks + ' checks passed');
