'use strict';
const assert = require('assert');
const {dom, t, doc} = require('./leftover_minors_world.cjs')();
for (const count of [3, 0]) {
  t.showBuildResult({ok:true, result:{qubits:['q1'],qubit_pairs:[]}, merge:{
    carried:0,grafted:0,residual_lost_total:0,calibration_changed_total:count,existing_entities_changed:count ? 1 : 0
  }}, 'D:/output');
  const loss = doc.querySelector('.gen-merge-loss');
  assert(loss && loss.textContent.includes('0 not carried'));
  assert.equal(loss.classList.contains('gen-merge-warn'), false);
  assert.equal(loss.classList.contains('gen-merge-ok'), true);
  if (count) {
    const cal = doc.querySelector('.gen-merge-cal-count');
    assert(cal.textContent.includes('3 values read differently'));
    assert(cal.classList.contains('gen-merge-warn'));
  }
  assert(doc.getElementById('gen-build-result').textContent.includes('existing entities changed: ' + (count ? 1 : 0)));
}
dom.window.close();
console.log('all checks passed');
