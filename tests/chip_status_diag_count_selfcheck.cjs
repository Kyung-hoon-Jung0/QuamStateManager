/* QA F-16 — the Health tile and the verdict banner count findings the way the
 * badges do.
 *
 * The toolbar badge ("⚠ N warnings") and the top-bar badge count through
 * diagnostics.summarize(), which lists an ADVISORY (an optional
 * recommendation, e.g. band-edge headroom) or an ACKNOWLEDGED finding (the
 * user said it is expected) without counting it as an issue. The Health tile
 * "structural issues" and the verdict banner counted every f.severity, so on
 * one page the tile and the badge beside it could disagree.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *   D1  one real warning + one advisory + one acknowledged error → the tile
 *       reads 1 issue, "0 err · 1 warn"
 *   D2  …and the banner is not 'fail' over an acknowledged error
 *   D3  only settled findings → the tile says none found
 *
 * Run: node tests/chip_status_diag_count_selfcheck.cjs
 *      (driven by tests/test_diagnostics_ui.py)
 */
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
const SRC = read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n' + read('chip-status.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const topo = {
  nodes: [{ id: 'q1', grid_location: '0,0', T1: 5e-5, metrics: { T1: { value: 5e-5 } } }],
  edges: [], summary: {},
};

function mount(findings) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
    + '<div id="topo-hero"></div><div id="topo-verdict-banner"></div>'
    + '<div id="topo-health-tiles"></div>'
    + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology' });
  const win = dom.window;
  win.UI_CONFIG = {};
  win.htmx = { process: function () {}, ajax: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(SRC).call(win);
  win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(topo)), rawWiring: {},
                         diagFindings: findings, metricMeta: {},
                         defaultThresholds: { T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' } } });
  return win;
}
function structural(win) {
  const tiles = win.document.querySelectorAll('#topo-health-tiles .topo-health-tile');
  for (let i = 0; i < tiles.length; i++) {
    const lab = tiles[i].querySelector('.tile-label');
    if (lab && lab.textContent === 'structural issues') {
      return { val: tiles[i].querySelector('.tile-val').textContent,
               sub: tiles[i].querySelector('.tile-sub').textContent,
               cls: tiles[i].className };
    }
  }
  return null;
}

const real = { severity: 'warning', category: 'config_orphan_pulse', location: 'pulses.const_pulse',
               message: 'pulse is defined but never referenced by any element', jump_path: '',
               advisory: false, acknowledged: null };
const advisory = { severity: 'warning', category: 'connectivity_band_edge', location: 'ports.x',
                   message: 'LO near band edge', jump_path: '', advisory: true, acknowledged: null };
const acked = { severity: 'error', category: 'env_unknown_field', location: 'qubits.q1.foo',
                message: 'unknown field', jump_path: 'qubits.q1.foo', advisory: false,
                acknowledged: { at: '2026-09-01' } };

{
  const win = mount([real, advisory, acked]);
  const t = structural(win);
  ok(t && t.val === '1', 'D1: one real warning is one issue — tile reads ' + (t && t.val));
  ok(t && /0 err\s*·\s*1 warn/.test(t.sub), 'D1: …"0 err · 1 warn" — ' + (t && t.sub));
  const banner = win.document.getElementById('topo-verdict-banner');
  ok(banner && !/fail/.test(banner.className) && !/⛔/.test(banner.textContent),
    'D2: an acknowledged error does not turn the banner red — ' + (banner && banner.className));
}
{
  const win = mount([advisory, acked]);
  const t = structural(win);
  ok(t && t.val === '✓' && /none found/.test(t.sub),
    'D3: only settled findings → none found — ' + (t && (t.val + ' | ' + t.sub)));
}

console.log(fails ? ('FAILED (' + fails + ')')
  : ('chip_status_diag_count_selfcheck ok (' + asserts + ' assertions)'));
process.exit(fails ? 1 : 0);
