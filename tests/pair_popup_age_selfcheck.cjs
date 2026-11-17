'use strict';
// The pair popup's "measured" line states the time of the number the 2Q badge
// shows: the time that number's own key records (time_ms), read through the
// shipped _edge2QAt, never the pair's cz_fidelity time (another key's).
const fs = require('fs');
const assert = require('assert');
const {JSDOM} = require('jsdom');
const file = process.argv[2] || 'quam_state_manager/web/static/chip-status.js';
const js = fs.readFileSync(file, 'utf8');
function slice(head, end) {
  const start = js.indexOf(head);
  assert(start >= 0, 'missing ' + head);
  const stop = js.indexOf(end, start);
  assert(stop > start, 'missing end of ' + head);
  return js.slice(start, stop).trim();
}
const win = new JSDOM('<body><div id="anchor"></div></body>', {runScripts: 'outside-only'}).window;
const DAY = 86400000, NOW = Date.UTC(2026, 10, 14);
win.NOW = NOW;
win.Function(`
 window.ChipStatus = {};
 ${slice('window.ChipStatus._opRows = function', '\n// "2 IRB values')}
 window.ChipStatus._gfConflictText = function(){ return ''; };
 var activePopup, closePopup = function(){}, TWOQ_SOURCES = [];
 var _esc = function(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); };
 var positionPopup = function(p){ document.body.appendChild(p); }, _ageClass = function(){ return 'age'; };
 var _ageLabel = function(ms){ return Math.round((window.NOW - ms) / 86400000) + ' days ago'; };
 window.TopoGraph = {closeDetails: function(){}};
 function _isIRBName(m) { return m === 'InterleavedRB' || m === 'IRB'; }
 function _edge2Q(e) { return null; }
 function _edge2QLabel() { return '2Q gate (IRB)'; }
 ${slice('function _edgeBest2Q(', '\n    function _edgeBestDerived')}
 var _e2qSrc = { key: 'rb2q_interleaved', match: _isIRBName,
   val: function(e, g) { return _edgeBest2Q(e, _isIRBName, g); } };
 ${slice('function _edge2QAt(e)', '\n    function default2QSource')}
 ${slice('function openPairPopup(', '\n        // Hand the ONE popup')}
 window.openPairPopup = openPairPopup;
`).call(win);
let n = 0;
function popup(edge) {
  win.openPairPopup(edge, win.document.getElementById('anchor'));
  const all = win.document.querySelectorAll('.topo-pair-popup');
  return all[all.length - 1];
}
function recency(edge) {
  const r = popup(edge).querySelector('.topo-popup-recency');
  return r ? r.textContent : null;
}
// the pair's cz_fidelity time is 69 days old (a Bell / SRB key); the IRB number shown was measured 2 days ago
const edge = {pair_id: 'p', cz_fidelity_updated_at: NOW - 69 * DAY,
  gate_fidelities: [{gate: 'cz', metric: 'InterleavedRB', value: .97, time_ms: NOW - 2 * DAY}]};
let t = recency(edge); n++;
assert.strictEqual(t, '2Q gate (IRB) measured 2 days ago', t);
assert(!/69 days/.test(t)); n++;
// the key records no time: an honest blank naming the leaf, never the pair's time
delete edge.gate_fidelities[0].time_ms;
t = recency(edge); n++;
assert.strictEqual(t, '2Q gate (IRB) age unknown: no time recorded at '
  + 'qubit_pairs.p.macros.cz.fidelity.InterleavedRB_updated_at', t);
// no time anywhere: still the honest line, naming the value and its leaf
t = recency({pair_id: 'p', gate_fidelities: [{gate: 'cz', metric: 'InterleavedRB', value: .97}]}); n++;
assert(/^2Q gate \(IRB\) age unknown: no time recorded at qubit_pairs\.p\./.test(t), t);
// a row that stores only average_gate_fidelity: the badge reads it, so the line names its leaf
t = recency({pair_id: 'p', gate_fidelities: [{gate: 'cz', metric: 'InterleavedRB', average_gate_fidelity: .97}]});
n++;
assert.strictEqual(t, '2Q gate (IRB) age unknown: no time recorded at '
  + 'qubit_pairs.p.macros.cz.fidelity.InterleavedRB_updated_at', t);
// the operating gate holds no IRB number (another macro does): the badge shows none, so no age
// line; the pair's newest-value row is named as such and is the one age on the popup
const opEdge = {pair_id: 'p', op_gate: 'cz_b', best_gate: 'cz_b', last_calibrated: NOW - 69 * DAY,
  gate_fidelities: [{gate: 'cz_a', metric: 'InterleavedRB', value: .975}]};
const pop = popup(opEdge);
t = recency(opEdge); n++;
assert.strictEqual(t, null, t);
n++;
assert(!/age unknown/.test(pop.textContent), pop.textContent);
n++;
assert(/newest value on this pair/.test(pop.textContent) && !/last calibrated/.test(pop.textContent),
  pop.textContent);
console.log('pair popup age checks passed (' + n + ' assertions)');
