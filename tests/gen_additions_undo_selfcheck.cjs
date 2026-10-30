// Z1: an additions edit is ONE wizard Ctrl+Z step, EXECUTED under jsdom.
//
// Drives the real _generate.html + generate.js + gen-additions.js; Ctrl+Z is
// window._wizUndo.tryUndo(), the call app.js's global handler makes while the
// wizard is mounted.
//
//  U1  + CZ… (additions AND populate.pairs.moving_qubit) undoes in one press
//  U2  + Add pulse undoes, and the list is repainted without it
//  U3  the Default select undoes to the previous default
//  U4  removing a gate undoes (the gate and the default that named it come back)
//  U5  every step reads as an additions step in the toast
//  U6  without the wizard's stack (another page) an edit still works and pushes nothing
//  U7  a populate.pairs bucket the CZ form created is removed again by the undo
//
// Run: node tests/gen_additions_undo_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', p), 'utf8');
const HTML = read('web/templates/_generate.html');
const GEN_JS = read('web/static/generate.js');
const TOPO_JS = read('web/static/topo-graph.js');
const ADD_JS = read('web/static/gen-additions.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
const tick = () => new Promise(r => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 8; i++) await tick(); }
const J = JSON.stringify;

const F = (raw, has_default, kind, default_repr) => ({ raw, has_default: !!has_default, kind: kind || 'plain', default_repr });
const ROLES = {
  ok: true, versions: { quam_builder: '0.6.0' }, qubit_fields: [],
  pulses: { 'pkg.pulses.SquarePulse': { origin: 'official', fields: { length: F('int'), amplitude: F('float') } } },
  macros_pair: { 'pkg.gates.CZGate': { origin: 'official',
    fields: { flux_pulse_qubit: F('Union[Pulse, str]', false, 'pointer_pulse') },
    cz: [{ field: 'flux_pulse_qubit', on: 'moving', required: true }] } },
  macros_qubit: {}, roots: {},
};

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' + HTML +
    '</div><div id="addhost"></div><div id="pt"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.toasts = [];
  win.showToast = function (m) { win.toasts.push(String(m)); };
  win.fetch = function (url) {
    url = String(url);
    const reply = (b) => win.Promise.resolve({ json: () => win.Promise.resolve(b) });
    if (/\/generate\/roles/.test(url)) return reply(ROLES);
    if (/\/generate\/additions\/lint/.test(url)) return reply({ ok: true, results: [] });
    return new win.Promise(function () {});
  };
  new win.Function(TOPO_JS).call(win);
  new win.Function(GEN_JS).call(win);
  new win.Function(ADD_JS).call(win);
  return win;
}

function hydrate(win) {
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'C', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }], opx_plus: [], octaves: [] },
    qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], twpas: [], pair_gate: 'cz_tunable',
    lines: [{ element: 'q1', line: 'drive' }, { element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }],
    populate: { qubit: {}, pairs: { 'q1-q2': { cz_variant: 'unipolar' } } },
  }, { mode: 'generate' });
  return G;
}

const TABLE = '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>x</th></tr></thead><tbody>' +
  '<tr class="gen-pop-setall"><td>Set all</td><td></td></tr>' +
  '<tr data-group="pairs" data-rid="q1-q2"><td>q1-q2</td><td></td></tr></tbody></table>';
const change = (w, sel, v) => { sel.value = v; sel.dispatchEvent(new w.Event('change', { bubbles: true })); };

async function mounted(win, G, withUndo) {
  const st = G._test.state;
  if (!withUndo) delete win._wizUndo;
  const ctx = { spec: () => st.spec, changed: () => {} };
  win.document.getElementById('pt').innerHTML = TABLE;
  win.GenAdditions.mount(win.document.getElementById('addhost'), ctx);
  win.GenAdditions.decoratePairTable(win.document.getElementById('gen-pop-tbl-pairs'), ctx);
  await settle();
  return st;
}
const row = (win) => win.document.querySelector('#gen-pop-tbl-pairs tr[data-rid="q1-q2"]');

function addCz(win, name, moving) {
  row(win).querySelector('.gen-add-czbtn').click();
  const f = win.document.querySelector('tr.gen-gates-formrow .gen-add-cz');
  change(win, f.querySelector('select.gen-add-cz-class'), 'pkg.gates.CZGate');
  f.querySelector('.gen-add-name').value = name;
  const sec = f.querySelector('.gen-add-cz-pulse');
  if (moving) change(win, sec.querySelector('select.gen-add-cz-on'), moving);
  change(win, sec.querySelector('select.gen-add-cz-pclass'), 'pkg.pulses.SquarePulse');
  sec.querySelector('.gen-add-cz-pname').value = name + '_pulse';
  sec.querySelector('input[data-field="length"]').value = '40';
  sec.querySelector('input[data-field="amplitude"]').value = '0.1';
  f.querySelector('.gen-add-ok').click();
}

(async () => {
  let win = makeWorld();
  let G = hydrate(win);
  let st = await mounted(win, G, true);
  const pairs0 = J(st.spec.populate.pairs);

  // U1: the CZ group writes additions + moving_qubit; one press undoes both
  addCz(win, 'cz_a', 'q2');
  ok((st.spec.additions || []).length === 2, 'U1 CZ group added (' + J(st.spec.additions) + ')');
  ok(st.spec.populate.pairs['q1-q2'].moving_qubit === 'target', 'U1 moving qubit set');
  ok(win._wizUndo.tryUndo() === true, 'U1 Ctrl+Z consumed');
  ok(st.spec.additions === undefined, 'U1 additions gone after ONE undo (' + J(st.spec.additions) + ')');
  ok(J(st.spec.populate.pairs) === pairs0, 'U1 populate.pairs back (' + J(st.spec.populate.pairs) + ')');
  ok(!row(win).querySelector('.gen-gate-added'), 'U1 the gate chip is repainted away');
  ok(/additions|gate cz_a/.test(win.toasts.join('|')), 'U5 toast names the step: ' + win.toasts.join('|'));

  // U2: + Add pulse
  const ch = win.document.querySelector('.gen-add-ch[data-target="qubits.q1.xy.operations"]');
  ch.querySelector('.gen-add-btn').click();
  const pf = ch.querySelector('.gen-add-form');
  change(win, pf.querySelector('select.gen-add-class'), 'pkg.pulses.SquarePulse');
  pf.querySelector('.gen-add-name').value = 'x_lab';
  pf.querySelector('input[data-field="length"]').value = '20';
  pf.querySelector('input[data-field="amplitude"]').value = '0.2';
  pf.querySelector('.gen-add-ok').click();
  ok((st.spec.additions || []).length === 1, 'U2 pulse added');
  ok(win.document.querySelector('#addhost .gen-add-item'), 'U2 listed');
  win._wizUndo.tryUndo();
  ok(st.spec.additions === undefined, 'U2 pulse undone');
  ok(!win.document.querySelector('#addhost .gen-add-item'), 'U2 list repainted without it');

  // U3: Default select
  addCz(win, 'cz_a', null);
  addCz(win, 'cz_b', null);
  const dsel = () => row(win).querySelector('select.gen-gate-default-sel');
  change(win, dsel(), 'cz_a');
  change(win, dsel(), 'cz_b');
  const dg = () => (st.spec.additions || []).filter(a => a.kind === 'default_gate').map(a => a.to).join();
  ok(dg() === 'cz_b', 'U3 default is cz_b');
  win._wizUndo.tryUndo();
  ok(dg() === 'cz_a', 'U3 one undo returns the previous default (' + dg() + ')');
  ok(dsel().value === 'cz_a', 'U3 the select is repainted');

  // U4: remove a gate (the default that names it goes with it), then undo
  const before = J(st.spec.additions);
  row(win).querySelector('.gen-gate-rm[data-gate="cz_a"]').click();
  ok(!st.spec.additions.some(a => a.name === 'cz_a' && a.kind === 'macro') && dg() === '', 'U4 gate and its default removed');
  win._wizUndo.tryUndo();
  ok(J(st.spec.additions) === before, 'U4 one undo brings both back');

  // U7: a spec with no populate.pairs at all: the CZ form creates the bucket, the undo removes it
  delete st.spec.populate.pairs;
  st.spec.additions = undefined;
  win.GenAdditions.mount(win.document.getElementById('addhost'), { spec: () => st.spec, changed: () => {} });
  addCz(win, 'cz_c', 'q2');
  ok(st.spec.populate.pairs && st.spec.populate.pairs['q1-q2'].moving_qubit === 'target', 'U7 bucket created');
  win._wizUndo.tryUndo();
  ok(st.spec.populate.pairs === undefined, 'U7 the created bucket goes with the undo (' + J(st.spec.populate.pairs) + ')');

  // U6: no wizard stack -> the edit still lands, nothing throws
  win = makeWorld();
  G = hydrate(win);
  st = await mounted(win, G, false);
  addCz(win, 'cz_a', null);
  ok((st.spec.additions || []).length === 2, 'U6 an edit works without the wizard stack');

  console.log((fails ? 'FAILED' : 'OK') + ' gen_additions_undo_selfcheck: ' + asserts + ' assertions, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
