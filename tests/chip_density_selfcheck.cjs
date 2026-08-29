// docs/141 §4o — per-PANEL tile size on Chip Status, against the REAL
// chip-status.js under jsdom. The Health-row "Tiles" slider is gone; every
// metric / 2Q-gate panel carries S · M · L right of its title:
//  - controlHtml(key) renders the three presets bound to that panel key, the
//    remembered one marked active
//  - a click (ONE delegated listener on the dashboard — panels are built
//    lazily) writes --topo-density-scale on THAT .topo-section only, never on
//    the dashboard, never on a sibling panel; L (1) removes the inline var
//  - the choice persists per panel (quam_chip_density_panels) and a fresh
//    page applies it through applyAll; garbage in storage is ignored; a
//    value outside 0.55–1.15 is clamped
//
// Run: node tests/chip_density_selfcheck.cjs   (needs jsdom)
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function world(storage) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
    + '<div class="topo-section" id="mp-a" data-density-panel="readout_frequency"><h4 class="topo-metric-panel-title" id="ta">Readout Frequency</h4><div class="topo-metric-topo-grid"></div></div>'
    + '<div class="topo-section" id="mp-b" data-density-panel="T1"><h4 class="topo-metric-panel-title" id="tb">T1</h4></div>'
    + '<div class="topo-section" data-topo-section="trends"></div>'
    + '<div class="topo-section" data-topo-section="fidelity" id="sec-fidelity"></div>'
    + '</div></div></body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  if (storage) win.localStorage.setItem('quam_chip_density_panels', storage);
  win.htmx = { ajax: function () {} };
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n' + read('chip-status.js')).call(win);
  // the builders put the control beside each title; here the fixture does it
  const D = win.ChipStatus.density;
  win.document.getElementById('ta').insertAdjacentHTML('beforeend', D.controlHtml('readout_frequency'));
  win.document.getElementById('tb').insertAdjacentHTML('beforeend', D.controlHtml('T1'));
  D.init();
  return win;
}

let win = world();
let doc = win.document;
const A = doc.getElementById('mp-a'), B = doc.getElementById('mp-b'), dash = doc.querySelector('.topo-dashboard');
const btns = (el) => Array.from(el.querySelectorAll('.density-preset[data-density-panel]'));
ok(btns(A).map((b) => b.textContent).join('') === 'SML' && btns(A).every((b) => b.getAttribute('data-density-panel') === 'readout_frequency'),
   'controlHtml renders S · M · L bound to the panel key');
ok(btns(A).filter((b) => b.classList.contains('active')).map((b) => b.textContent).join('') === 'L', 'with nothing remembered, L is active');
ok(A.style.getPropertyValue('--topo-density-scale') === '' && dash.style.getPropertyValue('--topo-density-scale') === '', 'no inline scale anywhere at rest');

btns(A)[1].click();                                    // M on panel A
ok(A.style.getPropertyValue('--topo-density-scale') === '0.85', 'M writes 0.85 on THAT panel (' + A.style.getPropertyValue('--topo-density-scale') + ')');
ok(B.style.getPropertyValue('--topo-density-scale') === '' && dash.style.getPropertyValue('--topo-density-scale') === '',
   'the sibling panel and the dashboard are untouched');
ok(btns(A).filter((b) => b.classList.contains('active')).map((b) => b.textContent).join('') === 'M', 'the active preset follows');
ok(JSON.parse(win.localStorage.getItem('quam_chip_density_panels')).readout_frequency === 0.85, 'persisted per panel key');
btns(B)[0].click();                                    // S on panel B
ok(B.style.getPropertyValue('--topo-density-scale') === '0.7' && A.style.getPropertyValue('--topo-density-scale') === '0.85', 'each panel keeps its own size');
btns(A)[2].click();                                    // L on panel A
ok(A.style.getPropertyValue('--topo-density-scale') === '' && JSON.parse(win.localStorage.getItem('quam_chip_density_panels')).readout_frequency === 1,
   'L removes the inline override (the CSS default, 1) and is remembered as 1');
ok(win.ChipStatus.density.get('T1') === 0.7 && win.ChipStatus.density.get('nope') === 1, 'get() answers per key, 1 when unknown');
win.ChipStatus.density.set('T1', 5);
ok(win.ChipStatus.density.get('T1') === 1.15, 'a value outside the range is clamped (' + win.ChipStatus.density.get('T1') + ')');

// a fresh page: the remembered sizes are applied at init, garbage ignored
win = world(JSON.stringify({ readout_frequency: 0.7, T1: 'abc' }));
doc = win.document;
ok(doc.getElementById('mp-a').style.getPropertyValue('--topo-density-scale') === '0.7', 'a remembered size is applied on a fresh page');
ok(doc.getElementById('mp-b').style.getPropertyValue('--topo-density-scale') === '', 'garbage in storage reads as the default');
ok(Array.from(doc.querySelectorAll('#mp-a .density-preset.active')).map((b) => b.textContent).join('') === 'S', 'and its preset shows active');
win = world('[1,2,3]');
ok(win.ChipStatus.density.get('T1') === 1, 'a non-object store is ignored');

// ── the jump guard: a section below Trends is re-anchored once Trends lands ──
// (docs/141 4o: Trends moved above Fidelity and is fetched lazily; a jump to
// Fidelity landed on the charts that arrived a moment later)
win = world();
doc = win.document;
const scrolled = [];
win.Element.prototype.scrollIntoView = function () { scrolled.push(this.id || this.getAttribute('data-topo-section')); };
const J = win.ChipStatus.jumpGuard;
const selOf = (v) => '[data-topo-section="' + v + '"]';
ok(J && typeof J.note === 'function' && typeof J.reanchor === 'function', 'the jump guard is a top-level core');
ok(J.reanchor(selOf) === false, 'nothing to re-anchor before any jump');
J.note('fidelity');
ok(J.reanchor(selOf) === true && scrolled.join(',') === 'sec-fidelity', 'a fresh jump to Fidelity is scrolled back to when Trends lands');
J.note('overview');
ok(J.reanchor(selOf) === false && scrolled.length === 1, 'a jump to a section ABOVE Trends is left alone (it cannot be displaced)');
J.note('coherence');
ok(J.reanchor(selOf) === false && scrolled.length === 1, 'a section that is not on the page yet is not scrolled to');
J.note('fidelity');
win.Date.now = (function (orig) { return function () { return orig() + 9000; }; })(win.Date.now);
ok(J.reanchor(selOf) === false, 'a jump older than the window is not re-anchored (the user has scrolled on)');

console.log(fails ? ('FAILED ' + fails) : 'chip_density_selfcheck: all ok');
process.exit(fails ? 1 : 0);
