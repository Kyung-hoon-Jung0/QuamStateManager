/* docs/118 — the dataset Interactive panel's stability rules, under jsdom with
 * the REAL app.js.
 *
 * The customer's report was "open Interactive, go somewhere, come back, and it
 * goes weird". Four independent mechanisms produced that, and each one is
 * pinned here because each one is invisible until it bites.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js');

const HTML = `<!doctype html><html><body>
  <div id="status-bar"></div>
  <div id="table-pane">
    <div id="ds-detail-root"><div class="dataset-detail">
      <div class="dataset-tabs">
        <a href="#" id="lnk-full" onclick="switchDatasetTab('full', this)">Full View</a>
        <a href="#" id="lnk-int" onclick="switchDatasetTab('interactive', this)">Interactive</a>
      </div>
      <div class="dataset-tab-content" id="ds-tab-combined" data-view="full"></div>
      <div class="dataset-tab-content hidden" id="ds-tab-interactive">
        <div id="ds-interactive-container" data-run-id="k:1" data-loaded="0"></div>
      </div>
    </div></div>
  </div>
  <div id="inspector-pane"></div>
</body></html>`;

const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;
global.window = w; global.document = w.document;
w.fetch = function () { return new Promise(function () {}); };   // never resolves
w.matchMedia = w.matchMedia || function () { return { matches: false, addListener() {}, removeListener() {} }; };

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' — ' + detail : '')); }
}

try { w.eval(fs.readFileSync(SRC, 'utf8')); } catch (e) {
  console.error('app.js failed to evaluate under jsdom: ' + (e && e.message));
  process.exit(1);
}

// ── 1. THE BIG ONE: a run opened as a FULL PAGE lives in #table-pane, not the
//    inspector. The old panel lookup fell back to #inspector-pane, so every
//    query below it found nothing and NO tab ever switched. ────────────────
const intTab = w.document.getElementById('ds-tab-interactive');
check('A1 the Interactive tab starts hidden', intTab.classList.contains('hidden'));
// jsdom with runScripts:'outside-only' does not fire inline onclick handlers,
// so invoke the handler with the link element — which is exactly what is under
// test here: the PANEL the handler resolves from that element.
w.switchDatasetTab('interactive', w.document.getElementById('lnk-int'));
check('A2 a full-page run switches tabs (panel resolved to its own detail root)',
      !intTab.classList.contains('hidden'), intTab.className);
check('A3 and the other tab is hidden',
      w.document.getElementById('ds-tab-combined').classList.contains('hidden'));

// ── 2. a purged tile must leave the render ledger ────────────────────────
const container = w.document.getElementById('ds-interactive-container');
function mkTile(vis) {
  const d = w.document.createElement('div');
  d.className = 'ds-interactive-plot';
  d.setAttribute('data-rendered', '1');
  d._isVisible = !!vis;
  container.appendChild(d);
  return d;
}
container._rendered = [];
const t1 = mkTile(false);
container._rendered.push(t1);
w.eval('window.__purge = function(el){ return _purgeInteractiveTile(el); }');
w.__purge(t1);
check('B1 a purged tile leaves container._rendered', container._rendered.length === 0,
      String(container._rendered.length));
check('B2 and is marked not-rendered', t1.getAttribute('data-rendered') === '0');

// ── 3. the hard cap must prefer OFFSCREEN tiles, and re-arm the observer for
//    a visible one it is forced to drop (an emptied tile never crosses an
//    intersection threshold on its own, so it would stay blank forever) ────
container._rendered = [];
const observed = [];
container._io = { unobserve() {}, observe(el) { observed.push(el); }, disconnect() {} };
const visibles = [];
for (let i = 0; i < 13; i++) visibles.push(mkTile(true));
const offscreen = mkTile(false);
container._rendered = visibles.concat([offscreen]);
w._pruneInteractiveTiles(container);
check('C1 the offscreen tile is purged first',
      offscreen.getAttribute('data-rendered') === '0');
const blanked = visibles.filter(t => t.getAttribute('data-rendered') === '0');
check('C2 a visible tile dropped by the hard cap is re-observed',
      blanked.length === 0 || observed.length >= blanked.length,
      'blanked=' + blanked.length + ' reobserved=' + observed.length);
check('C3 the ledger is back within the cap', container._rendered.length <= 12,
      String(container._rendered.length));

// ── 4. markup round-tripped through a STRING has no Plotly behind it, so it
//    must not keep claiming to be loaded/rendered ──────────────────────────
container.setAttribute('data-loaded', '1');
container._rendered = [mkTile(true)];
const corpse = container.querySelector('.ds-interactive-plot');
corpse.innerHTML = '<div class="js-plotly-plot"><svg class="main-svg"></svg></div>';
w._reviveInteractiveMarkup(w.document.getElementById('table-pane'));
check('D1 the container no longer claims to be loaded',
      container.getAttribute('data-loaded') === '0');
check('D2 tiles no longer claim to be rendered',
      corpse.getAttribute('data-rendered') === '0');
check('D3 the corpse markup is gone', corpse.innerHTML === '', corpse.innerHTML.slice(0, 40));

// ── 5. the resize helper only touches VISIBLE plots (resizing a hidden one is
//    a no-op that Plotly still pays for) ───────────────────────────────────
const resized = [];
w.Plotly = { Plots: { resize(el) { resized.push(el); } }, purge() {} };
const list = w.document.createElement('div');
list.className = 'ds-interactive-list';
const shown = w.document.createElement('div'); shown.className = 'js-plotly-plot';
const hidden = w.document.createElement('div'); hidden.className = 'js-plotly-plot';
list.appendChild(shown); list.appendChild(hidden);
container.appendChild(list);
Object.defineProperty(shown, 'offsetParent', { get: () => container });
Object.defineProperty(hidden, 'offsetParent', { get: () => null });
w.resizeInteractiveTiles(container);
check('E1 a visible plot is resized', resized.indexOf(shown) >= 0);
check('E2 a hidden plot is not', resized.indexOf(hidden) < 0);

if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
// app.js starts its own pollers under jsdom; exit explicitly so the harness
// does not wait on an event loop that never drains.
process.exit(0);
