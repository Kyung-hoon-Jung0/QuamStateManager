// Behavioral check for the r5 readability batch (app.js):
//  - rollingStats / trendStatTraces (moving average + ±σ band for the
//    history/trend charts — "read like a statistics figure")
//  - setUiScale (global zoom 0.8–1.5, persisted, label sync)
//  - sidebar compare multi-select (shift-click range + live count + clear)
//  - folder-browser dataset-mode highlighting (is-dataset rows + badge)
//
// Run: node tests/ui_readability_selfcheck.cjs   (needs jsdom)
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

const ROOT = path.join(__dirname, '..');
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

// Minimal DOM: sidebar tree with 5 compare checkboxes + the compare form +
// settings scale label + folder-browser dialog shells.
const DOM = `
  <div id="sidebar-tree">
    <ul>` +
  [1, 2, 3, 4, 5].map(function (i) {
    return '<li class="tree-entry"><div class="tree-entry-label">' +
      '<input type="checkbox" name="paths" value="/w/run' + i + '" form="compare-form">' +
      '<span class="entry-name">run' + i + '</span></div></li>';
  }).join('') + `
    </ul>
  </div>
  <form id="compare-form">
    <button type="submit" class="btn-compare">Compare Selected</button>
    <button type="submit" class="btn-trend">Trend Tracker</button>
    <button type="button" id="compare-clear" hidden>Clear</button>
  </form>
  <button id="ui-scale-value">100%</button>
  <dialog id="folder-browser">
    <input id="browser-selected-path">
    <div id="browser-newfolder-row" hidden>
      <input id="browser-newfolder-name"><span id="browser-newfolder-err"></span>
    </div>
    <div id="browser-recent-list"></div>
    <div id="browser-breadcrumbs"></div>
    <div id="browser-list"></div>
  </dialog>
  <input id="ds-target">`;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  win.HTMLDialogElement.prototype.close = function () { this.open = false; };
  win._fetchImpl = function () { return Promise.reject(new Error('no impl')); };
  win.fetch = function (url, opts) { return win._fetchImpl(url, opts); };
  win.AbortController = function () {
    this.signal = {}; this.abort = function () {};
  };
  new win.Function(APP_JS).call(win);
  return win;
}

(async function main() {

  // R1: rollingStats — flat series has σ 0; window clamps at edges.
  {
    const win = makeWorld();
    const st = win.rollingStats([5, 5, 5, 5, 5, 5], 3);
    ok(st.mean.every(function (m) { return m === 5; }), 'R1: flat mean');
    ok(st.std.every(function (s) { return s === 0; }), 'R1: flat σ = 0');
    const st2 = win.rollingStats([0, 10, 0, 10, 0, 10, 0, 10], 4);
    ok(st2.mean.length === 8 && st2.std[3] > 0, 'R1: alternating series has σ > 0');
  }

  // R2: trendStatTraces — band + MA shape; skips short/NaN-heavy series.
  {
    const win = makeWorld();
    const x = ['a', 'b', 'c', 'd', 'e', 'f', 'g'];
    const y = [1, 2, 3, 4, 5, 6, 7];
    const tr = win.trendStatTraces(x, y, { color: '#4f9cf9' });
    ok(tr.length === 3, 'R2: three traces (upper, lower+fill, MA)');
    ok(tr[1].fill === 'tonexty' && /rgba\(79,156,249,0\.14\)/.test(tr[1].fillcolor),
      'R2: band fill derives from the line color');
    ok(/moving avg/.test(tr[2].name) && tr[2].line.color === '#4f9cf9',
      'R2: MA line named + colored');
    ok(tr[0].hoverinfo === 'skip' && tr[0].showlegend === false,
      'R2: band edges silent in legend/hover');
    // Every mean sits between the band edges.
    ok(tr[2].y.every(function (m, i) { return m >= tr[1].y[i] && m <= tr[0].y[i]; }),
      'R2: mean within the band');
    ok(win.trendStatTraces(['a', 'b', 'c'], [1, 2, 3], {}).length === 0,
      'R2: short series (n<5) skipped');
    ok(win.trendStatTraces(x, [1, null, NaN, 2, null, 3, null], {}).length === 0,
      'R2: too few FINITE points skipped');
  }

  // R3: setUiScale — steps, clamps, persists, label syncs.
  {
    const win = makeWorld();
    ok(win.setUiScale(1) === 1.1, 'R3: +1 step → 110%');
    ok(win.document.documentElement.style.zoom == 1.1, 'R3: zoom applied');
    ok(win.localStorage.getItem('quam_ui_scale') === '1.1', 'R3: persisted');
    ok(win.document.getElementById('ui-scale-value').textContent === '110%',
      'R3: label synced');
    for (var i = 0; i < 10; i++) win.setUiScale(1);
    ok(win.localStorage.getItem('quam_ui_scale') === '1.5', 'R3: clamps at 150%');
    ok(win.setUiScale(0) === 1, 'R3: reset → 100%');
    ok(win.document.documentElement.style.zoom === '', 'R3: zoom cleared at 100%');
    for (var j = 0; j < 10; j++) win.setUiScale(-1);
    ok(win.localStorage.getItem('quam_ui_scale') === '0.8', 'R3: clamps at 80%');
  }

  // R3b (r6 item 3): explorerSetScale — Explorer-only zoom on the two tree
  // containers, clamped + persisted + slider/preset sync; stacks with the
  // global scale (independent storage keys).
  {
    const win = makeWorld();
    ['explorer-tree-state', 'explorer-tree-wiring'].forEach(function (id) {
      var d = win.document.createElement('div'); d.id = id;
      win.document.body.appendChild(d);
    });
    var slider = win.document.createElement('input');
    slider.id = 'explorer-scale-slider'; slider.type = 'range'; slider.value = '1';
    win.document.body.appendChild(slider);
    ok(win.explorerSetScale(1.25) === 1.25, 'R3b: set 125%');
    ok(win.document.getElementById('explorer-tree-state').style.zoom == 1.25 &&
       win.document.getElementById('explorer-tree-wiring').style.zoom == 1.25,
      'R3b: both trees zoomed');
    ok(win.localStorage.getItem('quam_explorer_scale') === '1.25', 'R3b: persisted');
    ok(parseFloat(slider.value) === 1.25, 'R3b: slider synced');
    ok(win.explorerSetScale(9) === 1.7 && win.explorerSetScale(0.1) === 0.75,
      'R3b: clamps to 0.75–1.7');
    win.explorerSetScale(1);
    ok(win.document.getElementById('explorer-tree-state').style.zoom === '',
      'R3b: zoom cleared at 100%');
    ok(win.localStorage.getItem('quam_ui_scale') === null ||
       win.localStorage.getItem('quam_ui_scale') === undefined ||
       win.localStorage.getItem('quam_ui_scale') !== '1.25',
      'R3b: independent of the global scale key');
  }

  // R4: sidebar multi-select — shift-click range, live count, clear.
  {
    const win = makeWorld();
    const all = win.document.querySelectorAll('#sidebar-tree input[name="paths"]');
    function click(el, shift) {
      // dispatched click events run the checkbox activation behavior in
      // jsdom (spec) — no manual toggle, or it double-flips.
      el.dispatchEvent(new win.MouseEvent('click',
        { bubbles: true, shiftKey: !!shift }));
    }
    click(all[0]);
    ok(win.document.querySelector('.btn-compare').textContent === 'Compare Selected',
      'R4: single selection keeps the plain label');
    click(all[3], true);   // shift-click: range 0..3 all checked
    ok(all[1].checked && all[2].checked && all[3].checked,
      'R4: shift-click checks the whole range');
    ok(win.document.querySelector('.btn-compare').textContent === 'Compare Selected (4)',
      'R4: live count on the Compare button');
    ok(win.document.getElementById('compare-clear').hidden === false,
      'R4: Clear chip visible while selected');
    // Shift-UNcheck clears the range too.
    click(all[1], false);   // plain click somewhere to move the anchor
    click(all[3], true);
    win.compareClearSelection();
    ok(Array.prototype.every.call(all, function (b) { return !b.checked; }),
      'R4: Clear unticks everything');
    ok(win.document.getElementById('compare-clear').hidden === true,
      'R4: Clear chip hides again');
  }

  // R5: folder browser dataset mode — is-dataset rows + badge; state mode
  // keeps the quam highlighting.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const dsMode = url.indexOf('kind=dataset') >= 0;
      return Promise.resolve({ ok: true, json: function () {
        return Promise.resolve({
          path: '/data', parent: '/',
          dirs: ['/data/#1_res_101010', '/data/quam_state', '/data/plain'],
          dataset_dirs: dsMode ? ['/data/#1_res_101010'] : undefined,
          has_dataset: dsMode ? false : undefined,
          has_quam_state: false
        });
      } });
    };
    function rowFor(p) {
      return win.document.querySelector(
        '#browser-list .browser-folder[data-path="' + p + '"]');
    }
    win.openFolderBrowser('ds-target', 'dataset');
    await tick();
    ok(rowFor('/data/#1_res_101010').classList.contains('is-dataset'),
      'R5: dataset run row highlighted in dataset mode');
    ok(!rowFor('/data/quam_state').classList.contains('is-quam'),
      'R5: quam_state row NOT quam-highlighted in dataset mode');
    // State mode (no kind): quam highlighting is back.
    win.openFolderBrowser('ds-target');
    await tick();
    ok(rowFor('/data/quam_state').classList.contains('is-quam'),
      'R5: quam_state highlighting in state mode');
    ok(!rowFor('/data/#1_res_101010').classList.contains('is-dataset'),
      'R5: no dataset tint in state mode');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('ui_readability_selfcheck: all checks passed');
  process.exit(0);   // app.js poll intervals keep the loop alive otherwise
})().catch(function (e) { console.error(e); process.exit(1); });
