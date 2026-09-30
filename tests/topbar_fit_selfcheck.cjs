/* jsdom selfcheck for window.TopbarFit (docs/231): the top bar walks its
 * width ladder by MEASUREMENT -- one tb-fit-N step at a time while a row
 * overflows -- instead of trusting the window width. Customer, 2026-09-30:
 * the title painted over the ⌗ link and the sync pill in the Sync-Qualibrate
 * frame at the L text size.
 *
 * jsdom computes no layout, so rects are driven by hand; the real-Chrome
 * measurement (zero overlaps 560-1920 px at M and L, with an unapplied edit)
 * lives in docs/231.
 *
 * Run: node tests/topbar_fit_selfcheck.cjs   (driven by tests/test_cust_0930_ui.py).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const START = 'window.TopbarFit = (function () {';
const i = src.indexOf(START);
if (i < 0) { console.error('FAIL: window.TopbarFit not found in app.js'); process.exit(1); }
const end = /\r?\n\}\)\(\);\r?\n/.exec(src.slice(i));
if (!end) { console.error('FAIL: end of the TopbarFit module not found'); process.exit(1); }
const block = src.slice(i, i + end.index + end[0].length);

const dom = new JSDOM(
    '<!doctype html><html><body><header class="topbar"><nav>' +
    '<ul id="left"><li id="l1">a</li><li id="l2">b<div id="pop">a wide open popover</div></li></ul>' +
    '<ul class="topbar-right" id="right"><li id="r1">search</li></ul></nav></header></body></html>',
    { url: 'http://localhost/qubits', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
Object.defineProperty(document, 'readyState', { get: () => 'complete', configurable: true });
const tb = document.querySelector('.topbar');
const $ = id => document.getElementById(id);

// Geometry model: the left row is 500 px wide; its content needs NEED[level]
// px, where level = how many tb-fit classes are on the bar right now.
let NEED = [700, 650, 600, 560, 520, 480];
const level = () => { let n = 0; while (tb.classList.contains('tb-fit-' + (n + 1))) n++; return n; };
const rect = (l, r) => ({ left: l, right: r, top: 0, bottom: 40, width: r - l, height: 40 });
$('left').getBoundingClientRect = () => rect(0, 500);
$('l1').getBoundingClientRect = () => rect(0, 100);
$('l2').getBoundingClientRect = () => rect(100, NEED[Math.min(level(), NEED.length - 1)]);
$('right').getBoundingClientRect = () => rect(500, 700);
$('r1').getBoundingClientRect = () => rect(500, 690);
// an open popover inside the row reports a huge scrollWidth -- must not count
Object.defineProperty($('left'), 'scrollWidth', { get: () => 5000 });
$('pop').getBoundingClientRect = () => rect(100, 3000);

new Function('window', 'document', block)(window, document);
const F = window.TopbarFit;
ok(typeof F === 'object' && typeof F.fit === 'function', 'the module loads');

ok(F.fit() === 5 && level() === 5, 'it steps until the row fits: 700 -> ... -> 480 <= 500 at level 5 (got ' + level() + ')');
ok(!tb.classList.contains('tb-fit-6'), 'and stops there: no step past the first one that fits');
ok(tb.getAttribute('data-fit') === '5', 'data-fit names the level');

NEED = [450];
ok(F.fit() === 0 && level() === 0, 'room again (a wider window, a shorter pill): every step is given back (got ' + level() + ')');
ok(!/tb-fit-/.test(tb.className), 'no tb-fit class left behind');

NEED = [9999];
ok(F.fit() === 11 && level() === 11, 'content that never fits stops at the floor (level 11 = wrap), no runaway (got ' + level() + ')');

NEED = [700, 650, 600, 450];
ok(F.fit() === 3, 'a pill that grows is re-measured from level 0 up (got ' + F.level + ')');

NEED = [450];
document.documentElement.classList.add('topbar-hidden');
F.fit();
ok(level() === 3, 'with the bar collapsed (☰) nothing is re-fitted -- there is no row to measure');
document.documentElement.classList.remove('topbar-hidden');

// the right-hand row is measured too
NEED = [450];
$('r1').getBoundingClientRect = () => rect(500, 760);
ok(F.fit() === 11, 'an overflowing right row counts as well');

if (fails) { console.error(fails + ' FAIL'); process.exit(1); }
console.log('all ok');
