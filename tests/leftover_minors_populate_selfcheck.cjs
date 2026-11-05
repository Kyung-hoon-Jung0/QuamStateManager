'use strict';
const assert = require('assert');
const { dom, t, doc } = require('./leftover_minors_world.cjs')();
t.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] }, merge: {
  carried: 0, grafted: 0, populate_protected: 3,
  populate_protected_detail: [
    { path: 'a', old: 2, new: 2, unchanged: true },
    { path: 'b', old: 2, new: 2, unchanged: true },
    { path: 'c', old: 2, new: 3, unchanged: false }
  ]
}}, 'D:/output');
const lines = [...doc.querySelectorAll('.gen-merge-pop-line')];
assert.equal(lines.filter(el => el.textContent.startsWith('edited ')).length, 1);
assert(doc.getElementById('gen-build-result').textContent.includes('2 edited values equal the source'));
dom.window.close();
console.log('all checks passed');
