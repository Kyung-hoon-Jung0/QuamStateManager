'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<!doctype html><html data-theme="dark"><body></body></html>',
    {url: 'http://localhost/instrument', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
const instrument = {controllers: {'1': {max_output_port: 8, fems: {'1': {type: 'lf-fem',
    output_ports: {'1': [{role: 'z', element: 'q0', label: 'q0'}]}, input_ports: {}}}}}};
w.htmx = {ajax() {}, trigger() {}, process() {}, on() {}};
w.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({instrument, wiring: {}}), text: () => Promise.resolve('')});
w.eval(fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/app.js'), 'utf8'));
const wait = () => new Promise(resolve => setTimeout(resolve, 30));
let checks = 0;
function check(condition, message) { checks++; assert.ok(condition, message); }
(async () => {
    w.FloatWiring.open(); await wait();
    const grid = () => d.querySelector('#float-wiring svg rect');
    check(grid() && grid().getAttribute('fill') === '#1e1e2e', 'dark rack starts with the dark palette');
    w.toggleTheme(); await wait();
    check(grid().getAttribute('fill') === '#f8f8f8', 'open rack follows the light palette');
    w.toggleTheme(); await wait();
    check(grid().getAttribute('fill') === '#1e1e2e', 'open rack follows the dark palette again');
    d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
    check(!d.getElementById('float-wiring'), 'Escape closes the floating panel without an inspector');
    console.log(checks + ' assertions passed'); dom.window.close();
})().catch(error => {console.error(error); dom.window.close(); process.exitCode = 1;});
