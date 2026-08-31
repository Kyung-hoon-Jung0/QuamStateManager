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
// the fine slider (the user asked it back): one per panel, bound to the key
const slA = A.querySelector('.topo-density-pslider[data-density-panel="readout_frequency"]');
const slB = B.querySelector('.topo-density-pslider[data-density-panel="T1"]');
ok(!!slA && !!slB && slA.min === '0.35' && slA.max === '1.15', 'each panel carries its own fine slider (0.35–1.15: the floor the user asked for)');
win.ChipStatus.density.set('T1', 0.1);
ok(win.ChipStatus.density.get('T1') === 0.35, 'a value under the floor clamps to 0.35 (' + win.ChipStatus.density.get('T1') + ')');
win.ChipStatus.density.set('T1', 0.7);
ok(slB.value === '0.7', 'the slider shows the panel\'s current size (' + slB.value + ')');
slB.value = '0.6'; slB.dispatchEvent(new win.Event('input', { bubbles: true }));
ok(B.style.getPropertyValue('--topo-density-scale') === '0.6' && JSON.parse(win.localStorage.getItem('quam_chip_density_panels')).T1 === 0.6,
   'dragging the slider sets and remembers THAT panel\'s size');
ok(btns(B).filter((b) => b.classList.contains('active')).length === 0, 'a between-preset size lights no preset');
btns(B)[1].click();
ok(slB.value === '0.85', 'a preset click moves the slider (' + slB.value + ')');
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
const _realNow = win.Date.now;
win.Date.now = (function (orig) { return function () { return orig() + 9000; }; })(win.Date.now);
ok(J.reanchor(selOf) === false, 'a jump older than the window is not re-anchored (the user has scrolled on)');
win.Date.now = _realNow;

/* docs/141 4ac -- the guard YIELDS TO THE USER, and the positive case above is
   not vacuous.

   R2-10 measured that setting WINDOW_MS to 0 left every §4o pin green: `note`
   and `reanchor` ran in the same millisecond, so "is the jump fresh?" was
   never actually asked. Advancing the clock a little between them is what
   makes the positive assertion mean something -- and it is the same clock
   move the negative case below needs. */
{
  const pane = doc.getElementById('table-pane') || (function () {
    const d = doc.createElement('div'); d.id = 'table-pane'; doc.body.appendChild(d); return d;
  })();
  const before = scrolled.length;

  // positive control WITH a real gap: a fresh jump, 100 ms later, still anchors
  J.note('fidelity', pane);
  win.Date.now = (function (orig) { return function () { return orig() + 100; }; })(_realNow);
  ok(J.reanchor(selOf) === true && scrolled.length === before + 1,
     'a jump made 100 ms ago is still re-anchored (the window is really consulted)');
  win.Date.now = _realNow;

  // and the user's own scroll cancels it
  J.note('fidelity', pane);
  pane.dispatchEvent(new win.Event('wheel'));
  win.Date.now = (function (orig) { return function () { return orig() + 100; }; })(_realNow);
  ok(J.reanchor(selOf) === false && scrolled.length === before + 1,
     'a wheel on the pane cancels the pending re-anchor (the user moved on)');
  win.Date.now = _realNow;

  // a keypress counts too, and re-noting re-arms
  J.note('fidelity', pane);
  pane.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'PageDown' }));
  win.Date.now = (function (orig) { return function () { return orig() + 100; }; })(_realNow);
  ok(J.reanchor(selOf) === false, 'a key on the pane cancels it too');
  win.Date.now = _realNow;
  J.note('fidelity', pane);
  win.Date.now = (function (orig) { return function () { return orig() + 100; }; })(_realNow);
  ok(J.reanchor(selOf) === true, 'a NEW jump re-arms the guard');
  win.Date.now = _realNow;
}

/* docs/141 4ac -- ?view=gate must reach Fidelity. TAB_SPEC has no `gate`
   entry (this range removed it, and a pin asserts its absence), so the
   deep-link guard rejected the alias before setChipStatusView could map it. */
{
  const src = read('chip-status.js');
  const i = src.indexOf('var _deepView = ');
  ok(i > 0, 'the deep-link path normalises the gate alias before the TAB_SPEC test');
  ok(/_deepView\s*=\s*\(_serverChipView === 'gate'\) \? 'fidelity' : _serverChipView/.test(src),
     'and it maps gate -> fidelity');
  const guard = src.slice(i, i + 400);
  ok(/if \(_deepView && TAB_SPEC\[_deepView\]\)/.test(guard),
     'the guard then tests the NORMALISED view, not the raw one');

  /* the guard can only yield to the user if the CALLER hands it the pane the
     user scrolls -- the harness drives jumpGuard directly, so only the source
     can say whether the production wrapper does. */
  ok(/jumpGuard\.note\(view,\s*_scrollPane\(\)\)/.test(src),
     'the production _jump.note passes the scroll pane, so the guard can arm on it');
}

console.log(fails ? ('FAILED ' + fails) : 'chip_density_selfcheck: all ok');
process.exit(fails ? 1 : 0);
