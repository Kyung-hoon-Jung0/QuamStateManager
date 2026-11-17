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
 var _e2qSrc = { key: 'rb2q_interleaved', match: _isIRBName,
   val: function(e) { var r = window.ChipStatus._opRows(e).filter(function(g){ return _isIRBName(g.metric); })[0];
                      return r ? r.value : null; } };
 ${slice('function _edge2QAt(e)', '\n    function default2QSource')}
 ${slice('function openPairPopup(', '\n        // Hand the ONE popup')}
 window.openPairPopup = openPairPopup;
`).call(win);
let n = 0;
function recency(edge) {
  win.openPairPopup(edge, win.document.getElementById('anchor'));
  const all = win.document.querySelectorAll('.topo-pair-popup');
  const r = all[all.length - 1].querySelector('.topo-popup-recency');
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
assert.strictEqual(t, 'age unknown: no time recorded at qubit_pairs.p.macros.cz.fidelity.InterleavedRB_updated_at', t);
// no time anywhere: still the plain honest line
t = recency({pair_id: 'p', gate_fidelities: [{gate: 'cz', metric: 'InterleavedRB', value: .97}]}); n++;
assert(/^age unknown/.test(t), t);
console.log('pair popup age checks passed (' + n + ' assertions)');
