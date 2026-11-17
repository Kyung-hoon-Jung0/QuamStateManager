// The "+ CZ" button says on its row why it is off, and the class count never
// reports "0 lab" classes when no lab module was read. EXECUTED under jsdom.
//
//  W1  classes not read yet: "+ CZ" is disabled and a visible note on the row names why
//  W2  classes read: "+ CZ" is enabled and the row carries no note
//  W3  no lab module read and none found: the count says so instead of "0 lab"
//  W4  a lab class found: the count still names the official and lab classes
//
// Run: node tests/gen_additions_czwhy_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const JS = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', 'gen-additions.js'), 'utf8');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
const tick = () => new Promise(r => setTimeout(r, 0));

const COLD = { ok: false, cold: true };
const READ = (pulses) => ({ ok: true, versions: {}, qubit_fields: [], pulses: pulses, macros_pair: {}, macros_qubit: {},
  roots: {} });

async function world(roles) {
  const dom = new JSDOM('<!doctype html><div id="host"></div><div id="pt"></div>', { runScripts: 'outside-only' });
  const w = dom.window;
  w.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(/lint/.test(String(url)) ? { ok: true, results: [] } : roles) });
  require('./gate_action_fetch.cjs').install(w);
  w.eval(JS);
  const spec = { qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], pair_gate: 'cz_tunable',
    lines: [{ element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }, { element: 'q1-q2', line: 'coupler' }],
    populate: { pairs: { 'q1-q2': { cz_variant: 'unipolar' } } } };
  const ctx = { spec: () => spec, changed: () => {} };
  w.document.getElementById('pt').innerHTML = '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>x</th></tr></thead>' +
    '<tbody><tr data-group="pairs" data-rid="q1-q2"><td>q1-q2</td><td></td></tr></tbody></table>';
  w.GenAdditions.mount(w.document.getElementById('host'), ctx);
  w.GenAdditions.decoratePairTable(w.document.getElementById('gen-pop-tbl-pairs'), ctx);
  await tick(); await tick();
  return w;
}

(async () => {
  let w = await world(COLD);
  let btn = w.document.querySelector('.gen-add-czbtn');
  let why = w.document.querySelector('tr[data-rid="q1-q2"] .gen-add-czwhy');
  ok(btn && btn.disabled, 'W1 + CZ is disabled before the classes are read');
  ok(why && /not been read yet/.test(why.textContent) && !why.hidden, 'W1 the row names why: ' + (why && why.textContent));

  w = await world(READ({ 'pkg.pulses.FlatTopGaussianPulse': { origin: 'official', fields: {} } }));
  btn = w.document.querySelector('.gen-add-czbtn');
  ok(btn && !btn.disabled, 'W2 + CZ is enabled once the classes are read');
  ok(!w.document.querySelector('.gen-add-czwhy'), 'W2 no note when the button works');
  const count = w.document.querySelector('.gen-add-count').textContent;
  ok(!/0 lab/.test(count) && /no lab module read/.test(count) && /^1 official/.test(count),
     'W3 the count says no lab module was read: ' + count);

  w = await world(READ({ 'pkg.pulses.FlatTopGaussianPulse': { origin: 'official', fields: {} },
                         'lab_pkg.flux.LabFluxPulse': { origin: 'lab', fields: {} } }));
  const count2 = w.document.querySelector('.gen-add-count').textContent;
  ok(/^1 official and 1 lab pulse classes/.test(count2), 'W4 a found lab class is counted: ' + count2);

  console.log((fails ? 'FAILED' : 'OK') + ' gen_additions_czwhy_selfcheck: ' + asserts + ' assertions, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
