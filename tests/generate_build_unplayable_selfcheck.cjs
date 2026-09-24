/* jsdom behavioral check (QA F12): a build whose QM config the QM will
 * reject does not headline as a plain success.
 *
 * The server names the channels with no RF/LO frequency in
 * result.elements_without_frequency and adds one ⚠ warning line; the
 * headline says "not runnable yet" and reads as a warning. A result without
 * the key (older servers, a clean chip) renders exactly as before.
 *
 * Run:  node tests/generate_build_unplayable_selfcheck.cjs
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
win.NumberInput = { fit() {}, attach() {}, format() {},
  strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
win.armPlainResize = function () {};
win.renderInstrumentWiring = function () {};
win.confirm = function () { return true; };
win.fetch = function () { return new win.Promise(function () {}); };
new win.Function(GEN_JS).call(win);
const G = win.QuamGen;
G.init();
const T = G._test;
const doc = win.document;

const W = "2 element(s) have no RF/LO frequency (q6.xy, q6.resonator): ...";
T.showBuildResult({ ok: true, result: {
  qubits: ['q1', 'q2', 'q3', 'q4', 'q5', 'q6'], qubit_pairs: [],
  warnings: [W], elements_without_frequency: ['qubits.q6.xy', 'qubits.q6.resonator']
} }, 'D:\\out');
let el = doc.getElementById('gen-build-result');
let head = el.querySelector('p');
ok(/Generated 6 qubits/.test(head.textContent) && /not runnable yet/.test(head.textContent),
   'U1: the headline says the chip is not runnable yet — got ' + head.textContent);
ok(head.classList.contains('gen-build-warn-line'), 'U1: and reads as a warning');
ok(Array.prototype.some.call(el.querySelectorAll('.gen-build-warn-line'),
     function (p) { return p.textContent.indexOf('q6.xy, q6.resonator') >= 0; }),
   'U2: the warning line names the elements');

T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [],
  elements_without_frequency: [] } }, 'D:\\out');
head = doc.getElementById('gen-build-result').querySelector('p');
ok(!/not runnable/.test(head.textContent) && !head.classList.contains('gen-build-warn-line'),
   'U3: a clean chip headlines as before');
T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] } }, 'D:\\out');
head = doc.getElementById('gen-build-result').querySelector('p');
ok(!/not runnable/.test(head.textContent), 'U3: a result without the key too');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('generate_build_unplayable_selfcheck: all checks passed');
