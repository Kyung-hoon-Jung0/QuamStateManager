'use strict';
// v1.5.0 customer bug: with a run of the LEFT data list clicked (keyboard focus there), Up/Down must
// step the runs only. The page-wide table-row arrow handler of app.js also moved the selection of the
// main pane's clickable table (the CZ gate plan's operation table) and scrolled the page with it.
// Outside the data list the table keeps its arrow navigation.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const app = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const marker = app.indexOf(' * Arrow-key navigation for clickable table rows.');
assert.ok(marker > 0, 'the table-row arrow handler is found');
const start = app.indexOf('(function() {', marker);
const end = app.indexOf('})();', start) + 5;
const dom = new JSDOM('<!doctype html><body>' +
    '<aside id="sidebar"><div id="sidebar-tree"><span class="tree-entry-click" data-uid="f:1" tabindex="0">#1</span>' +
    '<span class="tree-entry-click" data-uid="f:2" tabindex="0">#2</span></div></aside>' +
    '<div id="table-pane"><table><tbody>' +
    '<tr class="clickable-row row-selected" id="r0"><td>a</td></tr><tr class="clickable-row" id="r1"><td>b</td></tr>' +
    '<tr class="clickable-row" id="r2"><td>c</td></tr></tbody></table><button id="other">x</button></div></body>',
    {runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
w.HTMLElement.prototype.scrollIntoView = function () { w.__scrolled = (w.__scrolled || 0) + 1; };
w.eval(app.slice(start, end));
let n = 0;
const ok = (c, m) => { assert.ok(c, m); n++; };
const sel = () => [...d.querySelectorAll('tr.row-selected')].map(r => r.id).join();
const press = (el, key) => {
    const ev = new w.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true});
    el.dispatchEvent(ev);
    return ev;
};

const entry = d.querySelector('.tree-entry-click');
entry.focus();
ok(d.activeElement === entry, 'setup: the clicked run of the data list holds the keyboard');
w.__scrolled = 0;
let ev = press(entry, 'ArrowDown');
ok(sel() === 'r0' && !w.__scrolled, 'ArrowDown on the data list leaves the main table alone and does not scroll it');
ok(!ev.defaultPrevented, 'and does not swallow the key the data list uses to step its runs');
press(entry, 'ArrowUp');
ok(sel() === 'r0', 'ArrowUp on the data list leaves the main table alone');
press(entry, 'Enter');
ok(sel() === 'r0', 'Enter on the data list does not open a table row');

entry.blur();
ok(d.activeElement === d.body, 'setup: focus left the data list');
ev = press(d.body, 'ArrowDown');
ok(sel() === 'r1' && ev.defaultPrevented && w.__scrolled === 1, 'elsewhere ArrowDown still moves the table selection');
const other = d.getElementById('other');
other.focus();
press(other, 'ArrowDown');
ok(sel() === 'r2', 'focus inside the main pane keeps the table navigation');
console.log(`table_arrow_focus_selfcheck: ${n} checks passed`);
w.close();
