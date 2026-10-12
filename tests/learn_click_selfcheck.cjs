/* The real click handler must use frozen evidence and the clicked panel. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function _attachInteractivePlotClickHandler(');
const end = source.indexOf('window._attachInteractivePlotClickHandler', start);
let updates = null;
const context = {window: {}, console,
    _dsRootFor: () => null, _getRunQubits: () => [], _runChipExpect: () => null,
    _dsRunUidOf: () => 'run-1', _showPlotClickToast: () => {},
    _openPlotApplyPopup: rows => { updates = rows; }};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
let handler;
const div = {on: (_, fn) => { handler = fn; }};
const target = {path: 'qubits.q0.xy.amplitude', axis: 'x', plotly_axis: 'x2',
    transform: {type: 'prefactor', old: 0.23},
    provenance: {formula: 'proposed, not applied'}};
context._attachInteractivePlotClickHandler(div, {qubit: 'q0', axis: 'x', targets: [target]}, 'run-1');
handler({points: [{x: 1.1, y: 0, fullData: {xaxis: 'x2'}}]});
assert(updates && Math.abs(updates[0].value - 0.253) < 1e-12, 'frozen prefactor is applied');
assert.strictEqual(updates[0].provenance.formula, 'proposed, not applied');
updates = null;
handler({points: [{x: 1.1, y: 0, fullData: {xaxis: 'x'}}]});
assert.strictEqual(updates, null, 'another panel never stages this target');
target.transform.coordinate_scale = 0.004;
target.transform.coordinate_offset = 0.1;
handler({points: [{x: 250, y: 0, fullData: {xaxis: 'x2'}}]});
assert(updates && Math.abs(updates[0].value - 0.253) < 1e-12, 'secondary prefactor maps the parent coordinate');
console.log('3 client checks passed');
