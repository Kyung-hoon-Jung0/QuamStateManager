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
 * w7 final-QA P3s:
 *  Q1     a capture (stateHistoryChanged) and the working copy moving
 *         (sm:wc-moved) re-fetch the metadata and repaint the meta line;
 *         a mount that never asked stays lazy;
 *  Q2     a tile jump (click / Enter / Space) writes ?view=<its tab> into the
 *         URL, keeping history.state, so F5 comes back to that tab;
 *  Q3     hovering a panel TITLE parks the label span's native tooltip too
 *         (one tooltip at a time) and the card carries its text, direction
 *         words included; the S / M / L and Show Meta Info tips stay;
 *  Q4     a scroll that leaves the hovered element under the pointer (the big
 *         chip's lazy growth) keeps the card and moves it with the element; a
 *         scroll that takes it away (or covers it) hides the card.
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
  nodes: [node('q1', '0,0', 2.0e-5, 5.0e9), node('q2', '1,0', 3.0e-5, 5.1e9), node('q3', '2,0', 1.4e-5, 5.2e9)],
  edges: [edge('q1-2', 'q1', 'q2'), edge('q2-3', 'q2', 'q3')],
  summary: {},
};
// the route's shape (GET /topology/metric-meta)
// S10 C7: old -> new, metadata fixtures carry the ledger's proven writer.
const META = {
  ok: true, mode: 'ledger', newest: '20260101_120000_000', snapshots: 4, updating: false,
  q: {
    T1: { q1: { ts: '20260101_120000_000', run: 31, provenance: 'run_proven', label: 'run #31', sub: '06 Ramsey', first: false, leaves: 1, value: 2.0e-5 },
          q2: { ts: '20251201_080000_000', run: null, provenance: 'first_record', label: 'first recorded', sub: 'writer unknown', first: true, leaves: 1, value: 3.0e-5 },
          q3: { ts: '20260101_110000_000', run: null, provenance: 'observed', label: 'observed', sub: 'writer unknown', first: false, leaves: 1, value: 1.4e-5 } },
  },
  p: { '2q:StandardRB:cz_SNZ': { 'q1-2': { load_id: 4085 } } },
};

let META_NOW = META;     // what the route answers now (Q1 moves it)
function makeWin(store, metricMeta) {
  const dom = new JSDOM(SHELL, { runScripts: 'outside-only', pretendToBeVisual: true,
                                 url: 'http://localhost/topology?view=overview' });
  const win = dom.window;
  if (store) Object.keys(store).forEach(function (k) { win.localStorage.setItem(k, store[k]); });
  win.htmx = { process: function () {}, ajax: function () {} };
  win._fetches = [];
  win.fetch = function (url) {
    win._fetches.push(String(url));
    if (/\/topology\/metric-meta/.test(url)) {
      const ans = JSON.parse(JSON.stringify(META_NOW));
      return Promise.resolve({ ok: true, json: function () { return Promise.resolve(ans); } });
    }
    return new Promise(function () {});
  };
  win._scrolled = [];
  win.Element.prototype.scrollIntoView = function () { win._scrolled.push(this); };
  new win.Function(SRC).call(win);
  win._plotlyRender = function () { return Promise.resolve(); };
  win.Plotly = win.Plotly || {};
  win.ChipStatus.mount({ topo: TOPO, rawWiring: {}, diagFindings: [], metricMeta: metricMeta || {}, defaultThresholds: {} });
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
  let d = MI.describe(META.q.T1.q1, { cur: 2.0e-5, now: NOW });
  ok(/^Last changed: /.test(d.lines[0]) && d.lines.some(l => /Written by: run #31.*06 Ramsey/.test(l)),
     'T1: a run-written change is "Last changed" + "Written by: run #31 \u00b7 06 Ramsey" -- ' + JSON.stringify(d.lines));
  ok(/^\d\d-\d\d \d\d:\d\d \u00b7 #31$/.test(d.tag) && !d.edited, 'T2: its tile line is an ABSOLUTE date + run -- ' + d.tag);
  d = MI.describe(META.q.T1.q3, { cur: 1.4e-5, now: NOW });
  ok(/^Last changed: /.test(d.lines[0]) && d.lines.some(l => /Recorded: observed.*writer unknown/.test(l)) && !/#/.test(d.tag),
     'T3: no run -> "Last changed" + the why, never a run number -- ' + JSON.stringify(d));
  d = MI.describe(META.q.T1.q2, { cur: 3.0e-5, now: NOW });
  ok(/^Unchanged since/.test(d.lines[0]) && d.tag.charAt(0) === '\u2264', 'T4: a first ledger row stays unchanged, tile "\u2264date" -- ' + JSON.stringify(d));
  d = MI.describe(META.q.T1.q1, { cur: 2.5e-5, now: NOW });
  ok(d.edited && d.tag === 'not in history' && /^Not in this chip/.test(d.lines[0]),
     'T5: a value on screen that differs from history\u2019s says "not in history" -- ' + JSON.stringify(d));
  d = MI.describe(META.q.T1.q1, { cur: 2.0e-5 * (1 + 1e-12), now: NOW });
  ok(!d.edited, 'T5b: a float-noise difference is not an edit');
  // S10 C7: old -> new, incomplete snapshot-index dating has no ledger subject.
  // verifier P1 (2026-09-26): a subtree metric (readout fidelity from a
  // confusion matrix) has no one value; the server's matches_current flag is
  // what says the matrix on screen is one history never held
  d = MI.describe({ ts: '20260101_120000_000', run: 31, trigger: 'experiment', first: false, provenance: 'run_proven', label: 'run #31', leaves: 4,
                    matches_current: false }, { cur: 0.9225, now: NOW });
  ok(d.edited && d.tag === 'not in history',
     'T5c: a subtree whose leaves differ from history (matches_current=false) says "not in history" -- ' + JSON.stringify(d));
  d = MI.describe({ ts: '20260101_120000_000', run: 31, trigger: 'experiment', first: false, provenance: 'run_proven', label: 'run #31', leaves: 4,
                    matches_current: true }, { cur: 0.9225, now: NOW });
  ok(!d.edited && /^Last changed: /.test(d.lines[0]), 'T5d: ...and a matching subtree keeps its date');
  // verifier P1: a value that APPEARED at a later snapshot (null before) is
  // its first record, never "unchanged since history began"
  d = MI.describe({ ts: '20260101_120000_000', run: 31, trigger: 'experiment', first: false, provenance: 'run_proven', label: 'run #31', leaves: 1,
                    value: 2.0e-5, appeared: true }, { cur: 2.0e-5, now: NOW });
  ok(/^First recorded: /.test(d.lines[0]) && d.tag.charAt(0) !== '\u2264' && !/Unchanged/.test(d.lines.join(' ')),
     'T5e: an appeared value is "First measured", no \u2264 tag -- ' + JSON.stringify(d));
  d = MI.describe({ ts: '20260101_110000_000', run: null, trigger: 'auto', first: false, provenance: 'observed', label: 'observed', leaves: 1,
                    value: 1.4e-5, appeared: true }, { cur: 1.4e-5, now: NOW });
  ok(/^First recorded: /.test(d.lines[0]), 'T5f: ...and "First recorded" when no run wrote it');
  // S10 C7: old -> new, saved and proven ledger runs replace writer guesses.
  d = MI.describe({ ts: '20260101_120000_000', run: 31, first: false,
                    value: 2.0e-5, provenance: 'run_saved', label: 'saved in #31', sub: 'writer not proven' },
                  { cur: 2.0e-5, now: NOW });
  ok(/^Last changed: /.test(d.lines[0]) && !/Written by/.test(d.lines.join(' '))
       && /Recorded: saved in #31.*writer not proven/.test(d.lines.join(' ')),
     'W1: a saved run is never named as writer');
  d = MI.describe({ ts: '20260101_120000_000', run: 12, first: false,
                    value: 2.0e-5, provenance: 'run_proven', label: 'run #12', sub: '25 T1' },
                  { cur: 2.0e-5, now: NOW });
  ok(/^Last changed: /.test(d.lines[0]) && /Written by: run #12.*25 T1/.test(d.lines.join(' '))
       && /#12$/.test(d.tag) && !/#31/.test(d.lines.join(' ') + d.tag),
     'W2: only the proven writer is named');
  d = MI.describe(null, {});
  ok(d.tag === '\u2014' && /^No change of this value/.test(d.lines[0]), 'T6: no entry -> the honest empty answer');
  d = MI.describe(null, { mode: 'preparing' });
  ok(/Preparing/.test(d.lines[0]), 'T6b: ...or "updating" while the index is rebuilt');
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
  // verifier follow-up: the panel line says "measured" only for a run-written
  // value; q3's value came from an auto snapshot, q2's predates history
  ok(line && /1 proven run write/.test(line.textContent) && /1 recorded change/.test(line.textContent)
       && /1 first recorded, writer unknown/.test(line.textContent),
     'D5c: the panel line counts a run-less change as recorded, not measured -- ' + (line && line.textContent));
  const f01 = doc.querySelector('.topo-section[data-density-panel="f_01"]');
  ok(f01 && !f01.classList.contains('topo-meta-on'), 'D5b: only THAT panel turned on');

  // Q1 w7 final-QA P3: Take Snapshot moved Versions / Trends 18 -> 19 while
  // every meta line kept "history: 18 snapshots" until a hover after the TTL
  ok(/history: 4 recorded events/.test(line.textContent), 'Q1a: (the line counts 4 recorded events) -- ' + line.textContent);
  META_NOW = Object.assign({}, META, { snapshots: 5, newest: '20260101_130000_000' });
  const nQ1 = metaFetches(win);
  const statQ1 = t1.querySelector('.topo-metric-panel-stat');
  statQ1.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));   // a card up across the capture
  const cardQ1 = function () { return (doc.getElementById('cs-meta-pop') || {}).textContent || ''; };
  ok(/history: 4 recorded events/.test(cardQ1()), 'Q1a2: (the panel card is up, saying 4 recorded events)');
  doc.body.dispatchEvent(new win.CustomEvent('stateHistoryChanged', { bubbles: true }));
  doc.body.dispatchEvent(new win.CustomEvent('stateHistoryChanged', { bubbles: true }));   // header + drift poll
  await tick(600);
  ok(metaFetches(win) === nQ1 + 1 && /history: 5 recorded events/.test(line.textContent),
     'Q1b: a capture re-fetches the metadata ONCE for a burst and repaints the line -- fetches +'
       + (metaFetches(win) - nQ1) + ' | ' + line.textContent);
  ok(/history: 5 recorded events/.test(cardQ1()), 'Q1b2: ...and a card up at the time says the new count too -- ' + cardQ1().slice(-60));
  doc.querySelector('.topo-dashboard').dispatchEvent(new win.MouseEvent('mouseleave', { bubbles: false }));
  META_NOW = Object.assign({}, META, { snapshots: 6 });
  doc.dispatchEvent(new win.CustomEvent('sm:wc-moved', { detail: { seq: 'b', prev: 'a' } }));
  await tick(600);
  ok(metaFetches(win) === nQ1 + 2 && /history: 6 recorded events/.test(line.textContent),
     'Q1c: the working copy moving (sm:wc-moved) re-fetches too -- ' + line.textContent);
  META_NOW = META;
  {
    const lazy = makeWin();
    await tick(100);
    lazy.document.body.dispatchEvent(new lazy.CustomEvent('stateHistoryChanged', { bubbles: true }));
    lazy.document.dispatchEvent(new lazy.CustomEvent('sm:wc-moved', {}));
    await tick(600);
    ok(metaFetches(lazy) === 0, 'Q1d: a mount that never asked for the metadata is not made to -- ' + metaFetches(lazy));
    // Q1e: an answer ASKED FOR after the move already is the new one (on a big
    // chip one answer is ~300 KB / ~0.5 s): not asked for twice
    lazy.document.dispatchEvent(new lazy.CustomEvent('sm:wc-moved', {}));
    const cbL = lazy.document.querySelector('.topo-section[data-density-panel="T1"] .topo-meta-cb');
    cbL.checked = true;
    cbL.dispatchEvent(new lazy.Event('change', { bubbles: true }));   // its fetch leaves after the move
    await tick(700);
    ok(metaFetches(lazy) === 1, 'Q1e: a fetch that left after the move is not repeated -- ' + metaFetches(lazy));
  }
  {
    // Q1f: ...but one that left BEFORE it (a stored-on mount's own fetch,
    // the move announced right after) is asked again
    const early = makeWin({ quam_chip_meta_panels: JSON.stringify({ T1: true }) });
    early.document.dispatchEvent(new early.CustomEvent('sm:wc-moved', {}));
    await tick(700);
    ok(metaFetches(early) === 2, 'Q1f: a fetch that left before the move is asked again -- ' + metaFetches(early));
  }

  // S10 walk: the chip-wide notes are said once per surface -- never in a
  // panel's line, never in a tile's card; an unreadable ledger is said once,
  // in its own words (never "no history snapshots")
  {
    const NOTE = '1 run whose saved chip identity does not match this chip\u2019s is not part of this chip\u2019s timeline: #9 in data.';
    META_NOW = Object.assign({}, META, { notes: [NOTE] });
    const wn = makeWin({ quam_chip_meta_panels: JSON.stringify({ T1: true }) });
    await tick(150);
    const secN = wn.document.querySelector('.topo-section[data-density-panel="T1"]');
    const lineN = secN.querySelector('.topo-metric-panel-meta');
    ok(lineN && /newest change/.test(lineN.textContent) && lineN.textContent.indexOf(NOTE) < 0,
       'N1: a panel line carries no chip-wide note -- ' + (lineN && lineN.textContent));
    const cellN = secN.querySelector('.heatmap-cell[data-qubit="q1"]');
    cellN.dispatchEvent(new wn.MouseEvent('mouseover', { bubbles: true }));
    const popN = wn.document.getElementById('cs-meta-pop');
    ok(popN && /Last changed/.test(popN.textContent) && popN.textContent.indexOf(NOTE) < 0,
       'N2: a tile card carries no chip-wide note -- ' + (popN && popN.textContent));
    const UNREAD = 'The change history could not be read (unreadable). Nothing older is shown in its place.';
    META_NOW = { ok: true, mode: 'unavailable', message: UNREAD, updating: false, q: {}, p: {}, snaps: {},
                 notes: [UNREAD] };
    const wu = makeWin({ quam_chip_meta_panels: JSON.stringify({ T1: true }) });
    await tick(150);
    const secU = wu.document.querySelector('.topo-section[data-density-panel="T1"]');
    const lineU = secU.querySelector('.topo-metric-panel-meta');
    ok(lineU && lineU.textContent === UNREAD,
       'N3: an unreadable ledger is said once in a panel line, nothing else -- ' + (lineU && lineU.textContent));
    secU.querySelector('.heatmap-cell[data-qubit="q1"]').dispatchEvent(new wu.MouseEvent('mouseover', { bubbles: true }));
    const popU = wu.document.getElementById('cs-meta-pop');
    const nU = popU ? popU.textContent.split(UNREAD).length - 1 : 0;
    ok(nU === 1 && !/history snapshots/.test(popU.textContent),
       'N4: ...and once in a tile card -- ' + (popU && popU.textContent));
    META_NOW = META;
  }

  // D7 hover: card up, native title parked; leave: card gone, title back
  const cell = t1.querySelector('.heatmap-cell[data-qubit="q1"]');
  const title0 = cell.getAttribute('title');
  cell.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  const pop = doc.getElementById('cs-meta-pop');
  ok(pop && /Last changed/.test(pop.textContent) && /q1/.test(pop.textContent) && !cell.hasAttribute('title'),
     'D7a: hovering a tile shows the card (tile text + metadata) and parks the native tooltip -- ' + (pop && pop.textContent));
  doc.querySelector('.topo-dashboard').dispatchEvent(new win.MouseEvent('mouseleave', { bubbles: false }));
  ok(!doc.getElementById('cs-meta-pop') && cell.getAttribute('title') === title0,
     'D7b: leaving hides the card and puts the tooltip back unchanged');

  // D9 verifier D1 (2026-09-27): "on" must be VISIBLE, not merely filled in.
  // The shipped style.css goes into this document and the checks read the
  // computed display -- a textContent check passed while a hide rule keyed on
  // the RB panel's WRAPPER section hid every tag of a panel switched on.
  const st = doc.createElement('style');
  st.textContent = read('style.css');
  doc.head.appendChild(st);
  const shown = function (el) { return !!el && win.getComputedStyle(el).display !== 'none'; };
  const rbKey = '2q:StandardRB:cz_SNZ';
  const rb = doc.querySelector('.topo-section[data-density-panel="' + rbKey + '"]');
  const rbCb = rb && rb.querySelector('.topo-meta-cb');
  ok(rb && rb.parentElement.closest('.topo-section'), 'D9a: the fixture’s RB panel sits inside a wrapper .topo-section');
  rbCb.checked = true;
  rbCb.dispatchEvent(new win.Event('change', { bubbles: true }));
  await tick(120);
  const rbTag = rb.querySelector('.heatmap-cell[data-pair="q1-2"] .heatmap-cell-meta');
  const rbLine = rb.querySelector('.topo-metric-panel-meta');
  ok(rb.classList.contains('topo-meta-on') && rbTag && rbTag.textContent && shown(rbTag) && shown(rbLine),
     'D9b: a NESTED panel switched on shows its tile line and its summary line (computed display) -- tag='
       + (rbTag && rbTag.textContent) + ' ' + (rbTag && win.getComputedStyle(rbTag).display)
       + ' line=' + (rbLine && win.getComputedStyle(rbLine).display));
  ok(shown(t1.querySelector('.heatmap-cell[data-qubit="q1"] .heatmap-cell-meta')) && shown(line),
     'D9c: a top-level panel switched on shows them too');
  const f01Tag = f01.querySelector('.heatmap-cell[data-qubit="q1"] .heatmap-cell-meta');
  ok(f01Tag && f01Tag.textContent && !shown(f01Tag) && !shown(f01.querySelector('.topo-metric-panel-meta')),
     'D9d: a panel left off keeps its filled-in lines hidden -- ' + (f01Tag && f01Tag.textContent));
  rbCb.checked = false;
  rbCb.dispatchEvent(new win.Event('change', { bubbles: true }));
  ok(!shown(rbTag) && !shown(rbLine), 'D9e: the nested panel switched back off hides them again');
  st.remove();

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
  // O3d verifier P3: Enter on a jump tile bubbles on to the pane, whose guard
  // listens for keydown as "the user took over" -- the jump key must not
  // cancel the jump it just made (real Chrome: 95 px vs a click's 67)
  JG.cancel();
  tile('t1').focus();
  tile('t1').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await tick(80);
  ok(JG.current() === 'sel:coherence:.topo-section[data-density-panel="T1"]',
     'O3d: an Enter jump stays live for the guard after its key reaches the pane -- ' + JG.current());
  doc.getElementById('table-pane').dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
  ok(JG.current() === null, 'O3e: ...while any other key on the pane still ends the jump');

  // Q2 w7 final-QA P3: a tile jump left the URL at ?view=overview, so F5 hung
  // on the debounced scroll record, which a big chip's load starved
  const here = function () { return win.location.pathname + win.location.search; };
  [['click', 't1', 'coherence'], ['Enter', 't1', 'coherence'], [' ', 't1', 'coherence']].forEach(function (c) {
    win.history.replaceState({ htmx: true, mark: c[0] }, '', '/topology?view=overview');
    if (c[0] === 'click') tile(c[1]).dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    else tile(c[1]).dispatchEvent(new win.KeyboardEvent('keydown', { key: c[0], bubbles: true, cancelable: true }));
    ok(here() === '/topology?view=' + c[2] && win.history.state && win.history.state.htmx === true
         && win.history.state.mark === c[0],
       'Q2 (' + JSON.stringify(c[0]) + '): a tile jump writes its tab into the URL, history.state kept -- '
         + here() + ' ' + JSON.stringify(win.history.state));
  });
  await tick(80);     // their landing frames, before O4 counts scrolls
  JG.cancel();
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

  // ---- Q3 w7 final-QA P3: the title hover showed the card AND the label
  // span's native "... Higher is better" -- two tooltips at once
  {
    const w3 = makeWin({}, { T1: { label: 'T1', abbr: 'T1', direction: 'higher',
                                   blurb: 'Energy-relaxation time. Longer is better.' } });
    await tick(100);
    const d3 = w3.document;
    const sec = d3.querySelector('.topo-section[data-density-panel="T1"]');
    const h4 = sec.querySelector('.topo-metric-panel-title');
    const lab = h4.querySelector('.metric-label');
    const tip0 = lab.getAttribute('title');
    const CTL = '.topo-meta-toggle [title], .topo-meta-toggle[title], .topo-density-ctl [title], .topo-density-ctl[title]';
    const ctlTips = function () { return Array.prototype.map.call(h4.querySelectorAll(CTL), function (n) { return n.getAttribute('title'); }); };
    const ctlTips0 = ctlTips();
    ok(tip0 && /better/i.test(tip0), 'Q3a: (the label carries the direction words in its native title) -- ' + tip0);
    lab.dispatchEvent(new w3.MouseEvent('mouseover', { bubbles: true, clientX: 40, clientY: 40 }));
    const pop3 = d3.getElementById('cs-meta-pop');
    const titled = Array.prototype.filter.call(h4.querySelectorAll('[title]'), function (n) {
      return !n.closest('.topo-meta-toggle, .topo-density-ctl'); });
    ok(pop3 && titled.length === 0 && !h4.hasAttribute('title'),
       'Q3b: with the card up, nothing under the title but the controls has a native tooltip -- '
         + titled.map(function (n) { return n.className + '=' + n.getAttribute('title'); }).join(' | '));
    const blurbEl = pop3 && pop3.querySelector('.cs-meta-pop-blurb');
    ok(blurbEl && blurbEl.textContent === tip0,
       'Q3c: the card carries the label text, direction words included -- ' + (blurbEl && blurbEl.textContent));
    const ctlTips1 = ctlTips();
    ok(ctlTips1.length > 0 && JSON.stringify(ctlTips1) === JSON.stringify(ctlTips0),
       'Q3d: the S / M / L and Show Meta Info tips are left alone (' + ctlTips1.length + ')');
    d3.querySelector('.topo-dashboard').dispatchEvent(new w3.MouseEvent('mouseleave', { bubbles: false }));
    ok(!d3.getElementById('cs-meta-pop') && lab.getAttribute('title') === tip0 && !lab.hasAttribute('data-meta-title'),
       'Q3e: leaving puts the label tooltip back unchanged');
    // a tile card is unchanged: no blurb line (its own title is the card head)
    const c3 = sec.querySelector('.heatmap-cell[data-qubit="q1"]');
    c3.dispatchEvent(new w3.MouseEvent('mouseover', { bubbles: true, clientX: 40, clientY: 40 }));
    ok(d3.getElementById('cs-meta-pop') && !d3.querySelector('#cs-meta-pop .cs-meta-pop-blurb'),
       'Q3f: a tile card has no blurb line');
    d3.querySelector('.topo-dashboard').dispatchEvent(new w3.MouseEvent('mouseleave', { bubbles: false }));

    // ---- Q4 w7 final-QA P3 (big chip): lazy growth scrolled the pane under a
    // still mouse and every scroll hid the card
    const pane = d3.getElementById('table-pane');
    let rect = { left: 10, right: 110, top: 20, bottom: 70 };
    c3.getBoundingClientRect = function () { return Object.assign({ width: rect.right - rect.left, height: rect.bottom - rect.top }, rect); };
    let hit = c3;
    d3.elementFromPoint = function () { return hit; };
    const title3 = c3.getAttribute('title');
    c3.dispatchEvent(new w3.MouseEvent('mouseover', { bubbles: true, clientX: 50, clientY: 40 }));
    const top0 = (d3.getElementById('cs-meta-pop') || { style: {} }).style.top;
    rect = { left: 10, right: 110, top: 30, bottom: 80 };        // the guard re-landed it 10 px lower
    pane.dispatchEvent(new w3.Event('scroll'));
    const popA = d3.getElementById('cs-meta-pop');
    ok(popA && !c3.hasAttribute('title') && top0 === '76px' && popA.style.top === '86px',
       'Q4a: a layout scroll that leaves the tile under the pointer keeps the card and moves it with the tile -- top '
         + top0 + ' -> ' + (popA && popA.style.top));
    hit = pane;                                                  // the sticky bar / another element now covers the point
    pane.dispatchEvent(new w3.Event('scroll'));
    ok(!d3.getElementById('cs-meta-pop') && c3.getAttribute('title') === title3,
       'Q4b: a scroll that leaves something else under the pointer hides the card, tooltip back');
    hit = c3;
    c3.dispatchEvent(new w3.MouseEvent('mouseover', { bubbles: true, clientX: 50, clientY: 40 }));
    ok(!!d3.getElementById('cs-meta-pop'), 'Q4c: (the card is back on a new hover)');
    rect = { left: 10, right: 110, top: 200, bottom: 250 };      // the reader scrolled it away
    pane.dispatchEvent(new w3.Event('scroll'));
    ok(!d3.getElementById('cs-meta-pop'), 'Q4d: a scroll that moves the tile out from under the pointer hides the card');
  }

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
