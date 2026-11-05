'use strict';
const assert = require('assert');
const {dom, g, t, doc} = require('./leftover_minors_world.cjs')();
g.init();
g.state.spec.qubits = ['q1', 'q2'];
g.state.spec.instruments.controllers = [{con:1, fems:[{slot:3, fem:'mw'}]}];
g.state.spec.populate = {qubit:{q1:{RF_freq:6e9}, q2:{RF_freq:3.4e9}}};
const xy = port => [{con:1,slot:3,port,io_type:'output'}];
g.state.allocation = {q1:{xy:xy(2)},q2:{xy:xy(3)}};
const calc = t.loBandFindings(t.computeLoAssignments());
const coupled = calc.warnings.filter(w => /coupled/.test(w.message));
console.log('coupled warnings:', coupled.length, 'unique:', new Set(coupled.map(w => w.message)).size);
assert.equal(coupled.length, 1);
t.loBandFindings(calc);
assert.equal(calc.warnings.filter(w => /coupled/.test(w.message)).length, 1, 'calculation also deduplicates');
const host = doc.getElementById('gen-pop-wiring-diagram');
host.innerHTML = '<svg><g class="iw-port-circle" data-element="q1" data-role="xy"></g>' +
  '<g class="iw-port-circle" data-element="q2" data-role="xy"></g></svg>';
t.renderConflicts([
  {message:'Band mismatch',members:[{group:'qubit',rid:'q1'}]},
  {message:'Band mismatch',members:[{group:'qubit',rid:'q2'}]}
], false);
assert.equal(doc.querySelectorAll('.gen-band-warn-line').length, 1);
assert.equal(host.querySelectorAll('.iw-port-conflict').length, 2);
dom.window.close();
console.log('all checks passed');
