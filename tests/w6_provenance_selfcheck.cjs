'use strict';
const fs = require('fs');
const assert = require('assert');
const {JSDOM} = require('jsdom');
const js = fs.readFileSync('quam_state_manager/web/static/chip-status.js', 'utf8');
// Exercise the shipped render/click functions with isolated DOM dependencies.
function fn(name) {
  const start = js.indexOf('function ' + name + '(');
  // Read the complete shipped function through its next peer declaration.
  const ends = {openPairPopup: '\n        // Hand the ONE popup', _hintLine: '\n    function _openSnapDataset',
    _openSnapDataset: '\n    /* The dataset uid'};
  return js.slice(start, js.indexOf(ends[name], start)).trim();
}
const win = new JSDOM('<body><div id="anchor"></div></body>', {runScripts:'outside-only'}).window;
win.Function(`
 var activePopup, _esc = String, closePopup = function(){}, pct = String;
 var positionPopup = function(p){document.body.appendChild(p)}, _ageClass=String, _ageLabel=String;
 window.TopoGraph = {closeDetails:function(){}};
 ${fn('openPairPopup')}
 window.openPairPopup=openPairPopup;
`).call(win);
const edge = {pair_id:'p', gate_fidelities:[{gate:'cz', metric:'InterleavedRB', value:.97, inferred_load_id:12}]};
win.openPairPopup(edge, win.document.getElementById('anchor'));
let popup = win.document.querySelector('.topo-pair-popup');
assert(popup.textContent.includes('inferred source, not proven'));
assert(popup.querySelector('a[href="/dataset/by-run/12?via=inferred"]'));
edge.gate_fidelities[0].load_id = 11;
win.openPairPopup(edge, win.document.getElementById('anchor'));
popup = win.document.querySelectorAll('.topo-pair-popup')[1];
assert(!popup.textContent.includes('inferred'));
assert(popup.querySelector('a[href="/dataset/by-run/11"]'));
let calls=[];
win.htmx={ajax:(...args)=>calls.push(args)};
win.Function(`
 var _pointUid = function(cd){return cd[3]};
 ${fn('_hintLine')}
 ${fn('_openSnapDataset')}
 window.hintLine=_hintLine; window.openSnap=_openSnapDataset;
`).call(win);
assert(win.hintLine({inferred_uid:'folder:12'}).includes('inferred source, not proven'));
win.openSnap({points:[{customdata:['point','','',null,null,'folder:12']}]});
assert.equal(calls[0][1], '/dataset/folder:12?via=inferred');
const unitEdge = {pair_id:'p', gate_fidelities:[], detuning:400};
win.openPairPopup(unitEdge);
popup = win.document.querySelectorAll('.topo-pair-popup')[2];
assert(!popup.textContent.includes('Hz'));
unitEdge.detuning_display = '0.4 kHz';
win.openPairPopup(unitEdge);
popup = win.document.querySelectorAll('.topo-pair-popup')[3];
assert(popup.textContent.includes('0.4 kHz'));
console.log('W6 popup, units, Trends and inferred navigation checks passed');
