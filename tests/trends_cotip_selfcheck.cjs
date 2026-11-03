/* Chip Status Trends: two series' marks on the same spot get ONE tooltip that
 * lists both (Plotly's own hover named one, often not the one under the
 * pointer). Drives the shipped chip-status.js under jsdom with linear axes.
 * Run: node tests/trends_cotip_selfcheck.cjs (driven by tests/test_trends_cotip.py) */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else console.log('ok - ' + m); }
const dom = new JSDOM('<!doctype html><html><body><div id="h"></div></body></html>',
    { url: 'http://localhost/chip-status', pretendToBeVisual: true, runScripts: 'outside-only' });
const w = dom.window;
w.htmx = { ajax() {}, on() {}, trigger() {}, process() {} };
w.fetch = () => new Promise(() => {});
w.eval(fs.readFileSync(path.join(STATIC, 'chip-status.js'), 'utf8'));
ok(typeof w._trendsCoTip === 'function', 'the combined-tooltip helper is loaded');
const axis = (scale) => ({ d2c: (v) => v, l2p: (v) => v * scale, _offset: 0 });
const xa = axis(10), ya = axis(10);
const host = w.document.getElementById('h');
host.data = [
    { name: 'q1', x: [1, 5], y: [10, 20], text: ['11.63 us · saved by #920', 'a'] },
    { name: 'q2', x: [1, 5], y: [30, 20.1], text: ['b', 'c'] },
    { name: 'q3', x: [1, 5], y: [10.2, 40], text: ['11.63 us · saved by #918', 'd'] },
];
w._trendsCoTip(host, { points: [{ x: 1, y: 10.2, xaxis: xa, yaxis: ya, curveNumber: 2 }] });
const tip = w.document.getElementById('trends-cotip');
ok(tip && !tip.hidden, 'overlapping marks open one combined tooltip');
const t = tip ? tip.textContent : '';
ok(/q1/.test(t) && /q3/.test(t) && /#920/.test(t) && /#918/.test(t), 'it lists both series with their own text: ' + t);
ok(!/q2/.test(t), 'a series elsewhere is not listed');
ok(host.classList.contains('trends-cotip-on'), "Plotly's single label is hidden meanwhile");
w._trendsCoTip(host, { points: [{ x: 1, y: 30, xaxis: xa, yaxis: ya, curveNumber: 1 }] });
ok(tip.hidden && !host.classList.contains('trends-cotip-on'), 'a lone mark keeps Plotly\'s own tooltip');
const css = fs.readFileSync(path.join(STATIC, 'ui-kit.css'), 'utf8');
ok(/\.ui-trends-cotip\s*\{[^}]*var\(--ui-surface-1\)/.test(css), 'the tooltip uses ui-kit surface tokens');
process.exit(fails ? 1 : 0);
