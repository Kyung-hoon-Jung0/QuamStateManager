// The chip layout above the Pulses list starts folded unless a person chose
// to keep it open; a person's choice ("open" / "0") is never overwritten, and
// a fold is applied once per element (a later rows refresh does not re-fold
// a map the person opened). Loads the REAL pulses.js in jsdom.
//
// Run: node tests/pulses_map_fold_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const PULSES_JS = fs.readFileSync(
  path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'pulses.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }

function page(saved, key) {
  const html = '<details class="cmap" id="component-map" data-open-key="' + (key || 'quam_pulses_map_open') +
    '" open><summary class="cmap-summary">Chip layout</summary><div class="cmap-body"></div></details>';
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + html + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  if (saved !== null) win.localStorage.setItem('quam_pulses_map_open', saved);
  new win.Function(PULSES_JS).call(win);
  return win;
}

function mapOpen(win) { return win.document.getElementById('component-map').hasAttribute('open'); }

// no choice yet: folded
let w = page(null);
ok(typeof w.PulsesPage.foldPulsesMap === 'function', 'foldPulsesMap is exported');
w.PulsesPage.foldPulsesMap();
ok(!mapOpen(w), 'no saved choice folds the map');
// the old "1" every view wrote chose nothing: folded
w = page('1'); w.PulsesPage.foldPulsesMap();
ok(!mapOpen(w), 'a legacy "1" folds the map');
// a person's open choice wins
w = page('open'); w.PulsesPage.foldPulsesMap();
ok(mapOpen(w), 'a saved "open" keeps the map open');
// nothing is written by the fold itself
w = page(null); w.PulsesPage.foldPulsesMap();
ok(w.localStorage.getItem('quam_pulses_map_open') === null, 'the fold writes no choice');
// once per element: a person opens it, a rows refresh does not re-fold it
w.document.getElementById('component-map').setAttribute('open', 'open');
w.PulsesPage.foldPulsesMap();
ok(mapOpen(w), 'a second call leaves a map the person opened open');
// another page's map (other open key) is never touched
w = page(null, 'quam_component_map_open'); w.PulsesPage.foldPulsesMap();
ok(mapOpen(w), 'a component page map is not folded');
// the summary is still the keyboard's way in
ok(!!w.document.querySelector('#component-map > summary'), 'the summary stays');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK (' + n + ' assertions)');
