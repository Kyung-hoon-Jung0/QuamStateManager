'use strict';
const assert = require('assert');
const world = require('./leftover_minors_world.cjs');
const field = 'cz_interaction_duration';
function type(w, el, value) {
  assert(el, 'input exists');
  el.value = String(value);
  el.dispatchEvent(new w.Event('input', { bubbles: true }));
  el.dispatchEvent(new w.Event('change', { bubbles: true }));
}
let runs = 0;
for (let repeat = 0; repeat < 10; repeat++) {
  for (const order of ['first', 'last', 'before']) {
    const { dom, w, g, t, doc } = world();
    g.init();
    g.goToStep(4);
    g.state.namesTouched = true;
    g.state.spec.qubits = ['q1', 'q2', 'q3', 'q4', 'q5', 'q6'];
    doc.getElementById('gen-qubit-count').value = 6;
    g.state.spec.instruments.controllers = [{con: 1, fems: [{slot:1, fem:'mw'}, {slot:2, fem:'lf'}]}];
    g.state.pairGate = 'cz_fixed';
    g.state.spec.qubit_pairs = [['q1','q2'], ['q1','q3'], ['q2','q3'], ['q2','q4'],
      ['q3','q5'], ['q4','q6'], ['q5','q6']];
    g.state.pairsTouched = true;
    g.state.spec.populate = { qubit: {}, pairs: {} };
    g.state.spec.qubits.forEach((q, i) => { g.state.spec.populate.qubit[q] = {RF_freq: 5e9 + i * 1e8}; });
    if (order === 'before') g.state.spec.populate.pairs['q1-q2'] = {[field]: 120};
    t.czAutoOrient();
    t.applyPreset({sections: {pairs: {defaults: {[field]: 100}}}}, false);
    g.goToStep(6);
    let cells = [...doc.querySelectorAll('[data-group="pairs"][data-field="' + field + '"]')];
    assert.equal(cells.length, 7);
    if (order === 'last') cells.reverse();
    cells.forEach(el => type(w, el, 120));
    type(w, doc.querySelector('[data-group="qubit"][data-rid="q1"][data-field="RF_freq"]'), 5.9);
    g.goToStep(5); g.goToStep(7);
    for (const p of g.state.spec.qubit_pairs) {
      assert.equal(g.state.spec.populate.pairs[p.join('-')][field], 120, order + ': typed duration survives');
    }
    runs++;
    dom.window.close();
  }
}
// A draft can hold both spellings, with a user edit under the noncanonical one.
for (const mode of ['generate', 'regenerate']) {
const { dom, w, doc, g, t } = world();
g.init();
g.state.mode = mode;
g.state.spec.qubits = ['q1', 'q2'];
g.state.spec.qubit_pairs = [['q2', 'q1']];
g.state.pairGate = 'cz_fixed';
g.state.spec.populate = {pairs: {'q2-q1': {[field]:100}}};
t.renderPopulateTables();
type(w, doc.querySelector('[data-group="pairs"][data-rid="q2-q1"][data-field="' + field + '"]'), 120);
g.state.spec.qubit_pairs = [['q1', 'q2']];
g.state.spec.populate.pairs['q1-q2'] = {[field]:100};
t.reconcilePopulatePairs();
assert.equal(g.state.spec.populate.pairs['q1-q2'][field], 120, 'touched value beats canonical prefill');
const touched = mode === 'regenerate' ? g.state.regenTouched : g.state.populateTouched;
assert(touched['pairs|q1-q2|' + field], 'touch marker follows the pair');
dom.window.close();
}
console.log('all checks passed; orientation sequences: ' + runs);
