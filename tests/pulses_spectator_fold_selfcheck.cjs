// The Pulses page: the spectator pulses of gates on one channel sit behind one fold
// row; its button opens and closes exactly those rows, never another channel's.
const fs = require('fs'), path = require('path');
const { JSDOM } = require('jsdom');
const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'pulses.js'), 'utf8');
const TABLE = '<table id="pulses-table"><tbody>'
    + '<tr data-pulse-path="qubits.q3.z.operations.const" id="cal"><td></td></tr>'
    + '<tr data-pulse-fold="q3.z"><td><button type="button" data-pulse-fold-toggle="q3.z" aria-expanded="false" id="tog">2 spectator pulses of CZ gates</button></td></tr>'
    + '<tr data-pulse-path="a" data-pulse-fold-member="q3.z" hidden id="m1"><td></td></tr>'
    + '<tr data-pulse-path="b" data-pulse-fold-member="q3.z" hidden id="m2"><td></td></tr>'
    + '<tr data-pulse-fold="q4.z"><td><button type="button" data-pulse-fold-toggle="q4.z" aria-expanded="false" id="tog4">1 spectator pulse of CZ gates</button></td></tr>'
    + '<tr data-pulse-path="c" data-pulse-fold-member="q4.z" hidden id="m4"><td></td></tr>'
    + '</tbody></table>';
const dom = new JSDOM('<input type="hidden" id="pulses-owner-pick"><div id="inspector-pane"></div>' + TABLE, { runScripts: 'outside-only' });
const w = dom.window;
w.fetch = () => new w.Promise(() => {});
try { w.eval(src); } catch (e) { /* page-only setup may throw; the fold listener is registered last */ }
let n = 0, fails = 0; const check = (c, m) => { n++; if (!c) { fails++; console.log('FAIL ' + m); } };
const $ = (id) => w.document.getElementById(id);
let rowClicks = 0;
$('m1').addEventListener('click', () => { rowClicks++; });
check($('m1').hidden && $('m2').hidden && !$('cal').hidden, 'folded by default: the spectator rows are hidden, the calibrated row is not');
$('tog').click();
check(!$('m1').hidden && !$('m2').hidden && $('tog').getAttribute('aria-expanded') === 'true', 'the fold opens to its rows');
check($('m4').hidden, 'another channel\'s fold stays closed');
check(rowClicks === 0, 'the fold button never opens a row under it');
$('m1').click();
check(rowClicks === 1, 'a row inside the fold is clicked as any pulse row');
$('tog').click();
check($('m1').hidden && $('m2').hidden && $('tog').getAttribute('aria-expanded') === 'false' && !$('cal').hidden, 'the fold closes again');
console.log(fails ? fails + ' failed' : 'pulses spectator fold: ' + n + ' assertions ok');
process.exit(fails ? 1 : 0);
