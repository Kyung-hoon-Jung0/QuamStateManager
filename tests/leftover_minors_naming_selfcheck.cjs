'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/generate.js'), 'utf8');
for (const [names, count, expected] of [
  [['qX1', 'qX2', 'qX3', 'qX4', 'qX5'], 6, 'qX6'],
  [['q1', 'q2', 'q3'], 4, 'q4'],
  [['alpha', 'beta'], 3, 'q1'],
  [['q1', 'q3'], 3, 'q4'],
  [['q1', 'q3', 'q2'], 4, 'q4']
]) {
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://localhost/' });
  const w = dom.window;
  w.fetch = () => new w.Promise(() => {});
  w.NumberInput = { fit() {}, attach() {}, strip: String };
  w.eval(js);
  const g = w.QuamGen;
  g.init();
  g.goToStep(4);
  g.state.mode = 'regenerate';
  g.state.namesTouched = true;
  g.state.spec.qubits = names;
  const input = w.document.getElementById('gen-qubit-count');
  input.value = count;
  input.dispatchEvent(new w.Event('change', { bubbles: true }));
  assert.equal(g.state.spec.qubits[count - 1], expected);
  const grown = JSON.stringify(g.state.spec.qubits);
  input.dispatchEvent(new w.FocusEvent('focusin', {bubbles:true}));
  input.value = 2;
  input.dispatchEvent(new w.Event('change', {bubbles:true}));
  w._wizUndo.tryUndo();
  assert.equal(JSON.stringify(g.state.spec.qubits), grown, 'undo restores the exact names');
  dom.window.close();
}
console.log('all checks passed');
