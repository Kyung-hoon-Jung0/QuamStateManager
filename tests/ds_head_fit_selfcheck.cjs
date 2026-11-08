/* compact G1: dsHeadFit moves the run header's meta to a second line only
 * when it does not fit on line 1 (never hides it). Runs the real function. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const app = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const src = app.slice(app.indexOf('function dsHeadFit('), app.indexOf('window.dsHeadFit = dsHeadFit;'));
assert.ok(src.length > 50, 'dsHeadFit is found');
const dom = new JSDOM('<div class="inspector-header ui-ds-head"><div class="inspector-title"></div>'
    + '<div class="ui-ds-meta"><button class="ds-note-toggle"></button></div><div class="inspector-actions"></div></div>', { runScripts: 'outside-only' });
const w = dom.window, d = w.document;
w.eval(src + ';window.dsHeadFit = dsHeadFit;');
const head = d.querySelector('.ui-ds-head'), meta = d.querySelector('.ui-ds-meta');
const set = (sw, cw) => {
    Object.defineProperty(meta, 'scrollWidth', { value: sw, configurable: true });
    Object.defineProperty(meta, 'clientWidth', { value: cw, configurable: true });
};
set(400, 120); w.dsHeadFit();
assert.ok(head.classList.contains('ui-ds-head-wrap'), 'meta that does not fit moves to line 2');
set(200, 300); w.dsHeadFit();
assert.ok(!head.classList.contains('ui-ds-head-wrap'), 'meta that fits stays on line 1');
const kit = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'ui-kit.css'), 'utf8');
assert.match(kit, /\.ui-ds-head-wrap > \.ui-ds-meta \{[^}]*order: 3;[^}]*overflow: visible/, 'line 2 shows every control');
assert.match(kit, /\.ui-pane-sliver \* \{ visibility: hidden; \}/, 'the sliver hides whole controls, borders included');
console.log('ds_head_fit_selfcheck ok');
