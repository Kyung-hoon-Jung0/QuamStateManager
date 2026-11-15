/* Chip Status after a staged core layout recommendation: the "structural
 * issues" tile and the verdict banner re-count from the re-read core findings
 * (through buildHealthSummary, the one counting path), so no stale warning
 * stays beside an empty recommendation slot.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js + load_check.js):
 *   R1  core warning + one other warning; re-read returns none -> 1 warn, banner warn
 *   R2  core warning only; re-read returns none -> none found, banner not warn
 *   R3  the finding comes back (Ctrl+Z, re-read) -> the count goes back up
 *
 * Run: node tests/chip_status_core_refresh_selfcheck.cjs (driven by tests/test_core_layout_recommend.py)
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
const SRC = read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n' + read('load_check.js')
  + '\n;\n' + read('chip-status.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function wait(ms) { return new Promise((r) => setTimeout(r, ms || 0)); }
// Wait until cond() holds, polling every 2 ms, capped at 2 s; false at the cap.
async function until(cond, capMs) {
  const end = Date.now() + (capMs || 2000);
  while (Date.now() < end) { if (cond()) return true; await wait(2); }
  return cond();
}

const topo = {
  nodes: [{ id: 'q1', grid_location: '0,0', T1: 5e-5, metrics: { T1: { value: 5e-5 } } }],
  edges: [], summary: {},
};
const CORE = { severity: 'warning', category: 'core_layout', location: 'con1 FEM 1',
               message: '10 / 8 cores on con1 FEM 1', jump_path: '', advisory: false, acknowledged: null,
               fixes: [{ action: 'set_value', dot_path: 'qubits.q1.xy.core', value: 'q1_core' }],
               preview: { controller: 'con1', fem: 1, button: true, panel_line: null,
                          cores: { limit: 8, before: 10, after: 5, firm_after: 5, estimated: false } } };
const OTHER = { severity: 'warning', category: 'config_orphan_pulse', location: 'pulses.const_pulse',
                message: 'pulse is defined but never referenced by any element', jump_path: '',
                advisory: false, acknowledged: null };
// The re-read form (finding_line): no advisory / acknowledged keys.
const CORE_LINE = { kind: 'fem_budget', category: 'core_layout', location: 'con1 FEM 1',
                    message: CORE.message, severity: 'warning', fixes: CORE.fixes, preview: CORE.preview };

function mount(findings) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
    + '<div id="topo-hero"></div><div id="topo-verdict-banner"></div>'
    + '<div id="topo-health-tiles"></div><div id="topo-health-worst"></div>'
    + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology' });
  const win = dom.window;
  win.UI_CONFIG = {};
  win.htmx = { process: function () {}, ajax: function () {} };
  win.__after = [];
  win.fetch = function (url) {
    if (String(url).indexOf('/diagnostics/core-findings') === 0) {
      return win.Promise.resolve({ ok: true, status: 200,
        json: function () { return win.Promise.resolve({ ok: true, findings: win.__after }); } });
    }
    return new win.Promise(function () {});
  };
  new win.Function(SRC).call(win);
  win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(topo)), rawWiring: {},
                         diagFindings: JSON.parse(JSON.stringify(findings)), metricMeta: {},
                         defaultThresholds: { T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' } } });
  return win;
}
function structural(win) {
  const tiles = win.document.querySelectorAll('#topo-health-tiles .topo-health-tile');
  for (let i = 0; i < tiles.length; i++) {
    const lab = tiles[i].querySelector('.tile-label');
    if (lab && lab.textContent === 'structural issues') return tiles[i].querySelector('.tile-sub').textContent;
  }
  return null;
}
function banner(win) { return win.document.getElementById('topo-verdict-banner').className; }
function slotHtml(win) { const s = win.document.querySelector('[data-core-rec-slot]'); return s ? s.innerHTML : null; }

(async () => {
  // R1
  let win = mount([CORE, OTHER]);
  ok(structural(win) === '0 err \u00b7 2 warn', 'R1 before: the tile counts both warnings');
  ok(slotHtml(win).indexOf('data-core-rec') >= 0, 'R1 before: the recommendation is shown');
  win.__after = [];
  await win.CoreRecommend.refresh();
  await until(() => structural(win) !== '0 err \u00b7 2 warn');
  ok(structural(win) === '0 err \u00b7 1 warn', 'R1 after: the remaining warning only, ' + structural(win));
  ok(/warn/.test(banner(win)), 'R1 after: the banner stays warn for the other warning');
  ok(slotHtml(win) === '', 'R1 after: the slot is empty');
  // R3: the finding comes back (undo, then a re-read)
  win.__after = [CORE_LINE];
  await win.CoreRecommend.refresh();
  await until(() => structural(win) === '0 err \u00b7 2 warn');
  ok(structural(win) === '0 err \u00b7 2 warn', 'R3: the count goes back up');
  ok(slotHtml(win).indexOf('data-core-rec') >= 0, 'R3: the recommendation is shown again');

  // R2
  win = mount([CORE]);
  ok(structural(win) === '0 err \u00b7 1 warn' && /warn/.test(banner(win)), 'R2 before: 1 warn, banner warn');
  win.__after = [];
  await win.CoreRecommend.refresh();
  await until(() => structural(win) === 'none found');
  ok(structural(win) === 'none found', 'R2 after: none found, ' + structural(win));
  ok(!/warn/.test(banner(win)), 'R2 after: the banner is no longer warn, ' + banner(win));

  console.log(`(${asserts} assertions)`);
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
