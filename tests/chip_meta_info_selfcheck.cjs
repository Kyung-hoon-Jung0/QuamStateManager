/* Chip Status queue items 4 + 10 against the REAL app.js + topo-graph.js +
 * chip-status.js under jsdom.
 *
 * Item 4 -- per-panel metadata:
 *  T1-T8  the pure text rules (ChipStatus.metaInfo.describe): "measured" only
 *         with a run, "changed" + the why without one, "unchanged since
 *         history began" for a first row, "not in history" when the value on
 *         screen differs from history's, the lab's load_id / stamp, the honest
 *         empty and updating answers, UTC snapshot stamps, absolute in-tile
 *         dates (an age printed into a tile goes stale);
 *  D1-D2  the "Show Meta Info" toggle sits right of S / M / L on every metric
 *         panel and every 2Q RB panel;
 *  D3     nothing is fetched on render (lazy);
 *  D4-D5  switching it on: stored per panel, the panel shows its summary line
 *         + a line per tile from ONE fetch, other panels untouched;
 *  D6     a re-mount (reload) with the choice stored fetches by itself;
 *  D7     hover: a card with the tile's text + metadata, the native tooltip
 *         parked while it is up and put back after;
 *  D8     switching it off forgets the choice.
 * Item 10 -- Overview tiles jump:
 *  O1-O2  tiles with a section carry their target; Calibration Age and 2Q
 *         Gate Length stay inert;
 *  O3     a click lands the panel (scrollIntoView on THAT element) and notes a
 *         live jump the guard can re-land; the kebab keeps its own click;
 *  O4     an inert tile moves nothing;
 *  O5     the guard re-lands a panel jump below Trends, never one above it.
 *
 * Run: node tests/chip_meta_info_selfcheck.cjs  (driven by tests/test_chip_metric_meta.py)
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

const SHELL = '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
  + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
  + '<div class="topo-section" data-topo-section="overview"><div class="topo-summary-cards" id="topo-overview-tiles"></div></div>'
  + '<div class="topo-section" data-topo-section="health"></div>'
  + '<div class="topo-section" data-topo-section="trends"></div>'
  + '<div class="topo-section" data-topo-section="fidelity"><div id="topo-2q-rb-panels" data-topo-section="2qrb"></div></div>'
  + '<div class="topo-section" data-topo-section="fid1q" id="sec-fidelity-1q"><div id="topo-fidelity-1q-panels"></div></div>'
  + '<div class="topo-section" data-topo-section="fidro" id="sec-readout"><div id="topo-fidelity-ro-panels"></div></div>'
  + '<div id="topo-metric-panels" data-topo-section="metrics"></div>'
  + '</div></div></body></html>';

function rec(v) { return { value: v, raw: v, physical: true, unresolved: false, verdict: null, updated_at: null }; }
function node(id, gl, t1, f01) {
  return { id: id, grid_location: gl, T1: t1, f_01: f01,
           metrics: { T1: rec(t1), f_01: rec(f01) } };
}
function edge(pid, s, t) {
  return { pair_id: pid, source: s, target: t, has_cz: true, gate_kind: 'cz', directed: false,
           active: null, best_gate: 'cz_SNZ', gate_fidelities: [
             { gate: 'cz_SNZ', metric: 'StandardRB', value: 0.97, level: 'clifford' },
             { gate: 'cz_SNZ', metric: 'InterleavedRB', value: 0.99, level: 'gate' }] };
}
const TOPO = {
  nodes: [node('q1', '0,0', 2.0e-5, 5.0e9), node('q2', '1,0', 3.0e-5, 5.1e9), node('q3', '2,0', 1.5e-5, 5.2e9)],
  edges: [edge('q1-2', 'q1', 'q2'), edge('q2-3', 'q2', 'q3')],
  summary: {},
};
// the route's shape (GET /topology/metric-meta)
const META = {
  ok: true, newest: '20260101_120000_000', snapshots: 4, updating: false,
  q: {
    T1: { q1: { ts: '20260101_120000_000', run: 31, trigger: 'experiment', first: false, leaves: 1, value: 2.0e-5 },
          q2: { ts: '20251201_080000_000', run: null, trigger: 'manual', first: true, leaves: 1, value: 3.0e-5 },
          q3: { ts: '20260101_110000_000', run: null, trigger: 'auto', first: false, leaves: 1, value: 1.4e-5 } },
  },
  p: { '2q:StandardRB:cz_SNZ': { 'q1-2': { load_id: 4085 } } },
  snaps: { '20260101_120000_000': { run: 31, short: '06 Ramsey', why: null, uid: null },
           '20260101_110000_000': { run: null, short: '', why: 'Modified externally', uid: null } },
};

function makeWin(store) {
  const dom = new JSDOM(SHELL, { runScripts: 'outside-only', pretendToBeVisual: true,
                                 url: 'http://localhost/topology?view=overview' });
  const win = dom.window;
  if (store) Object.keys(store).forEach(function (k) { win.localStorage.setItem(k, store[k]); });
  win.htmx = { process: function () {}, ajax: function () {} };
  win._fetches = [];
  win.fetch = function (url) {
    win._fetches.push(String(url));
    if (/\/topology\/metric-meta/.test(url)) {
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve(META); } });
    }
    return new Promise(function () {});
  };
  win._scrolled = [];
  win.Element.prototype.scrollIntoView = function () { win._scrolled.push(this); };
  new win.Function(SRC).call(win);
  win._plotlyRender = function () { return Promise.resolve(); };
  win.Plotly = win.Plotly || {};
  win.ChipStatus.mount({ topo: TOPO, rawWiring: {}, diagFindings: [], metricMeta: {}, defaultThresholds: {} });
  return win;
}
const tick = (ms) => new Promise(function (r) { setTimeout(r, ms || 60); });
const metaFetches = (win) => win._fetches.filter(function (u) { return /metric-meta/.test(u); }).length;

(async function () {
  let win = makeWin();
  let doc = win.document;
  const MI = win.ChipStatus.metaInfo;
  const NOW = Date.UTC(2026, 0, 2, 12, 0, 0);

  // ---- T: the pure rules
  let d = MI.describe(META.q.T1.q1, { snaps: META.snaps, cur: 2.0e-5, now: NOW });
  ok(/^Last measured: /.test(d.lines[0]) && d.lines.indexOf('Written by: run #31 \u00b7 06 Ramsey') >= 0,
     'T1: a run-written change is "Last measured" + "Written by: run #31 \u00b7 06 Ramsey" -- ' + JSON.stringify(d.lines));
  ok(/^\d\d-\d\d \d\d:\d\d \u00b7 #31$/.test(d.tag) && !d.edited, 'T2: its tile line is an ABSOLUTE date + run -- ' + d.tag);
  d = MI.describe(META.q.T1.q3, { snaps: META.snaps, cur: 1.4e-5, now: NOW });
  ok(/^Last changed: /.test(d.lines[0]) && d.lines.indexOf('Written by: Modified externally') >= 0 && !/#/.test(d.tag),
     'T3: no run -> "Last changed" + the why, never a run number -- ' + JSON.stringify(d));
  d = MI.describe(META.q.T1.q2, { snaps: META.snaps, cur: 3.0e-5, now: NOW });
  ok(/^Unchanged since/.test(d.lines[0]) && d.tag.charAt(0) === '\u2264', 'T4: a first row is "unchanged since history began", tile "\u2264date" -- ' + JSON.stringify(d));
  d = MI.describe(META.q.T1.q1, { snaps: META.snaps, cur: 2.5e-5, now: NOW });
  ok(d.edited && d.tag === 'not in history' && /^Not in this chip/.test(d.lines[0]),
     'T5: a value on screen that differs from history\u2019s says "not in history" -- ' + JSON.stringify(d));
  d = MI.describe(META.q.T1.q1, { snaps: META.snaps, cur: 2.0e-5 * (1 + 1e-12), now: NOW });
  ok(!d.edited, 'T5b: a float-noise difference is not an edit');
  d = MI.describe(null, {});
  ok(d.tag === '\u2014' && /^No change of this value/.test(d.lines[0]), 'T6: no entry -> the honest empty answer');
  d = MI.describe(null, { updating: true });
  ok(/updating/.test(d.lines[0]), 'T6b: ...or "updating" while the index is rebuilt');
  d = MI.describe({ load_id: 4085 }, {});
  ok(d.tag === '#4085' && /#4085/.test(d.lines.join(' ')), 'T7: the lab node\u2019s load_id is named when history has nothing -- ' + JSON.stringify(d));
  ok(MI.snapMs('20260101_000000') === Date.UTC(2026, 0, 1) && MI.snapMs('garbage') === null,
     'T8: a snapshot stamp is UTC, and an unparsable one is refused');

  // ---- D: the toggle and the decoration
  const h4s = Array.prototype.slice.call(doc.querySelectorAll('.topo-section[data-density-panel] .topo-metric-panel-title'));
  ok(h4s.length >= 4 && h4s.every(function (h) {
       const t = h.querySelector('.topo-meta-toggle'), c = h.querySelector('.topo-density-ctl');
       return t && c && c.nextElementSibling === t && /Show Meta Info/.test(t.textContent);
     }), 'D1: every panel title carries "Show Meta Info" right after its S / M / L (' + h4s.length + ' panels)');
  ok(!!doc.querySelector('.topo-section[data-density-panel="2q:StandardRB:cz_SNZ"] .topo-meta-cb[data-meta-panel="2q:StandardRB:cz_SNZ"]'),
     'D2: the 2Q RB panels carry it too, keyed by the panel\u2019s own density key');
  await tick(100);
  ok(metaFetches(win) === 0, 'D3: nothing is fetched on render (lazy) -- ' + metaFetches(win));

  const t1 = doc.querySelector('.topo-section[data-density-panel="T1"]');
  const cb = t1.querySelector('.topo-meta-cb');
  cb.checked = true;
  cb.dispatchEvent(new win.Event('change', { bubbles: true }));
  await tick(120);
  const stored = JSON.parse(win.localStorage.getItem('quam_chip_meta_panels') || '{}');
  ok(stored.T1 === true && t1.classList.contains('topo-meta-on') && metaFetches(win) === 1,
     'D4: on -> stored per panel, class on, ONE fetch -- ' + JSON.stringify(stored) + ' fetches=' + metaFetches(win));
  const line = t1.querySelector('.topo-metric-panel-meta');
  const stat = t1.querySelector('.topo-metric-panel-stat');
  const q1tag = (t1.querySelector('.heatmap-cell[data-qubit="q1"] .heatmap-cell-meta') || {}).textContent;
  ok(line && stat && stat.nextElementSibling === line && /newest change/.test(line.textContent) && /#31/.test(q1tag || ''),
     'D5: the summary line sits under the stat line, each tile gets its line -- ' + (line && line.textContent) + ' | ' + q1tag);
  const f01 = doc.querySelector('.topo-section[data-density-panel="f_01"]');
  ok(f01 && !f01.classList.contains('topo-meta-on'), 'D5b: only THAT panel turned on');

  // D7 hover: card up, native title parked; leave: card gone, title back
  const cell = t1.querySelector('.heatmap-cell[data-qubit="q1"]');
  const title0 = cell.getAttribute('title');
  cell.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const pop = doc.getElementById('cs-meta-pop');
  ok(pop && /Last measured/.test(pop.textContent) && /q1/.test(pop.textContent) && !cell.hasAttribute('title'),
     'D7a: hovering a tile shows the card (tile text + metadata) and parks the native tooltip -- ' + (pop && pop.textContent));
  doc.querySelector('.topo-dashboard').dispatchEvent(new win.MouseEvent('mouseleave', { bubbles: false }));
  ok(!doc.getElementById('cs-meta-pop') && cell.getAttribute('title') === title0,
     'D7b: leaving hides the card and puts the tooltip back unchanged');

  // D8 off
  cb.checked = false;
  cb.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(!t1.classList.contains('topo-meta-on') && win.localStorage.getItem('quam_chip_meta_panels') === null,
     'D8: off -> class off, the choice forgotten (default elided)');

  // ---- O: Overview tiles jump
  const tile = function (id) { return doc.querySelector('#topo-overview-tiles .topo-card[data-tile-id="' + id + '"]'); };
  ok(tile('t1') && tile('t1').getAttribute('data-tile-jump') === 'coherence|.topo-section[data-density-panel="T1"]'
     && tile('t1').getAttribute('role') === 'link' && tile('t1').getAttribute('tabindex') === '0',
     'O1: the T1 tile carries its panel as a link -- ' + (tile('t1') && tile('t1').getAttribute('data-tile-jump')));
  ok(tile('cal_age') && !tile('cal_age').hasAttribute('data-tile-jump')
     && tile('gate2q_len') && !tile('gate2q_len').hasAttribute('data-tile-jump'),
     'O2: Calibration Age and 2Q Gate Length stay inert');
  ok(tile('in_spec') && tile('in_spec').getAttribute('title') === 'Jump to Health',
     'O2b: a composite tile with no hover card says where it jumps in a tooltip');

  win._scrolled.length = 0;
  tile('t1').querySelector('.ov-tile-menu').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(60);
  ok(win._scrolled.length === 0, 'O3a: the tile\u2019s kebab keeps its own click (no jump)');
  const pop2 = doc.getElementById('ov-tile-popover'); if (pop2) pop2.remove();
  tile('t1').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(80);
  const landed = win._scrolled[win._scrolled.length - 1];
  ok(landed && landed.getAttribute && landed.getAttribute('data-density-panel') === 'T1',
     'O3b: a click lands THE T1 panel -- ' + (landed && landed.getAttribute && landed.getAttribute('data-density-panel')));
  const JG = win.ChipStatus.jumpGuard;
  ok(JG.current() === 'sel:coherence:.topo-section[data-density-panel="T1"]' && JG.baseView(JG.current()) === 'coherence',
     'O3c: the jump is noted for the guard, under its tab -- ' + JG.current());
  win._scrolled.length = 0;
  tile('cal_age').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  await tick(80);
  ok(win._scrolled.length === 0, 'O4: an inert tile click moves nothing');
  // O5 the guard: a panel jump below Trends re-lands, one above it does not
  JG.note('sel:coherence:.topo-section[data-density-panel="T1"]', doc.getElementById('table-pane'));
  win._scrolled.length = 0;
  const re1 = JG.reanchor(function (v) { return v.replace(/^sel:[^:]+:/, ''); });
  JG.note('sel:health:[data-topo-section="health"]', doc.getElementById('table-pane'));
  const re2 = JG.reanchor(function (v) { return v.replace(/^sel:[^:]+:/, ''); });
  ok(re1 === true && re2 === false, 'O5: the guard re-lands a panel jump below Trends (' + re1 + '), never one above it (' + re2 + ')');

  // ---- D6 reload with the choice stored: the fetch happens by itself
  win = makeWin({ quam_chip_meta_panels: JSON.stringify({ T1: true }) });
  doc = win.document;
  await tick(150);
  const t1b = doc.querySelector('.topo-section[data-density-panel="T1"]');
  ok(t1b.classList.contains('topo-meta-on') && t1b.querySelector('.topo-meta-cb').checked && metaFetches(win) === 1
     && /#31/.test((t1b.querySelector('.heatmap-cell[data-qubit="q1"] .heatmap-cell-meta') || {}).textContent || ''),
     'D6: a re-mount with the choice stored comes up on and fetches by itself -- fetches=' + metaFetches(win));

  console.log(fails ? ('FAILED (' + fails + ')') : ('chip_meta_info_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
