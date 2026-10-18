/* P1-13 (UX round) -- "Fill calculator" from a resonator. The REAL calc.js
 * under jsdom: a button carrying the resonator's RF / LO / IF (Hz) opens the
 * Calculator, opens its RF = LO + IF section, writes RF and LO in GHz and
 * derives the IF in MHz exactly as typing them would, and names the source.
 * A resonator with only LO + IF gets its RF derived.
 *
 * Run: node tests/calc_fill_selfcheck.cjs
 * (driven by tests/test_v151_ux_resonator_lo_if.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const body = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates',
                                       '_calc_body.html'), 'utf8')
    .replace(/\{#[\s\S]*?#\}/g, '').replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '');
const dom = new JSDOM('<!doctype html><html><head></head><body>' +
    '<button id="calc-btn" class="calc-btn" type="button">calc</button>' +
    '<div id="calc-popover" class="calc-popover calc-hidden">' + body + '</div>' +
    '<button id="fill" data-rf="7250000000.0" data-lo="7000000000.0" data-if="" data-label="q1 resonator">f</button>' +
    '<button id="fill2" data-rf="" data-lo="6000000000.0" data-if="-120000000.0" data-label="q2 resonator">f</button>' +
    '</body></html>',
    { url: 'http://localhost/qubits', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

window.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'calc.js'), 'utf8'));

const v = function (id) { return document.getElementById(id).value; };
ok(typeof window.calcFillFromEl === 'function', 'calcFillFromEl is exposed');
const sec = document.getElementById('calc-s4-rf').closest('details');
ok(sec && !sec.open, 'precondition: the RF = LO + IF section is closed');
window.calcFillFromEl(document.getElementById('fill'));
ok(!document.getElementById('calc-popover').classList.contains('calc-hidden'), 'the Calculator opened');
ok(sec.open, 'the RF = LO + IF section opened');
ok(parseFloat(v('calc-s4-rf')) === 7.25 && parseFloat(v('calc-s4-lo')) === 7,
   'RF and LO written in GHz: ' + v('calc-s4-rf') + ' / ' + v('calc-s4-lo'));
ok(parseFloat(v('calc-s4-if')) === 250, 'IF derived in MHz: ' + v('calc-s4-if'));
ok(/q1 resonator/.test(document.getElementById('calc-s4-src').textContent), 'the source is named');

window.calcFillFromEl(document.getElementById('fill2'));
ok(Math.abs(parseFloat(v('calc-s4-rf')) - 5.88) < 1e-9, 'RF derived from LO + IF: ' + v('calc-s4-rf'));
ok(!document.getElementById('calc-popover').classList.contains('calc-hidden'), 'an open Calculator stays open');

console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
process.exit(fails ? 1 : 0);
