/* w9 uxpolish -- on a big chip the 2Q RB section and the metric panels are
 * built panel by panel, the panel a jump goes to FIRST.
 *
 * Measured in real Chrome on big30x (96 2Q panels, 69 pairs each; 19 metric
 * panels x 30 qubits) before the change: an Overview tile / tab press that
 * needed those sections was a 0.7-1.1 s task (making every panel, then laying
 * out ~7,000 cells at once) before its jump could even start. Now the press
 * makes the target panel and the ones under it (~130 cells); the rest follow
 * in small slices once the jump has come to rest, and the page ends up
 * exactly as the one-shot build makes it.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom, the
 * layout model + virtual clock of tests/chip_status_place_selfcheck.cjs, its
 * ResizeObserver delivered in the next frame as Chrome does; a 30-qubit
 * lattice with 49 pairs and 20 RB panels = 980 cells, 15 metrics = 450 cells):
 *  S1  an IRB tile press builds the IRB heading's first panels in the press
 *      (a chunk, not the section) and the jump lands on the IRB heading
 *  S2  no slice runs while the jump's smooth scroll is in flight
 *  S3  every panel arrives, each INTO its own display:contents slot; the
 *      section is then EXACTLY what the one-shot build makes (markup, order,
 *      paint, the reader's stored tile size and Show Meta Info), and the IRB
 *      heading is still under the sticky bar after the panels above it grew
 *  S4  no chart is drawn before the last panel is in; then all of them are,
 *      the jump's target first
 *  S5  a pair cell of a panel built in a later slice opens that pair
 *  S6  a changed-vs-live pair is marked in a panel built in a later slice
 *  S7  a T1 tile press builds no 2Q panel in the press; T1 lands, and stays
 *      under the bar while the whole 2Q section grows above it; the metric
 *      charts wait for the 2Q slices too
 *  S8  a second jump while the slices run builds ITS target at once; each
 *      panel is still made once (all charts, one each; the builder finishes)
 *  S9  navigating away stops the slices
 *  S10 F5 on a place inside a 2Q panel deep in the list: that panel is built
 *      at once and lands at its offset, and stays there as the rest arrive
 *  S11 F5 at 0 / 0.3 / 2 s after an IRB / T1 / readout jump lands exactly
 *      (the matrix the place selfcheck runs on the 5Q chain, on the big chip)
 *  S12 a 5-qubit chain still builds every panel in the press (nothing sliced)
 *  S13 the lazy observer seeing the 2Q section while its slices run changes
 *      nothing; S14 the pane reaching it builds its TOP panels first
 *  S15 a chart pump already drawing pauses while the slices run
 *  S16 Plotly itself is loaded only once the slices are done
 *  S17 the metric panels: a T1 press builds T1 and the ones under it, not
 *      the 1Q / readout panels above; all arrive, the three metric hosts end
 *      EXACTLY as the one-shot build; a later panel's qubit cell opens it; a
 *      Read. Fid. / Frequencies tab press builds that section's panels first;
 *      F5 inside the T2 Echo panel builds it at once and lands in it
 *
 * Run: node tests/chip_rb_slices_selfcheck.cjs   (driven by tests/test_chip_status.py)
 */
'use strict';

let P;
try {
  P = require('./chip_status_place_selfcheck.cjs');
} catch (e) {
  if (/jsdom/.test(String(e && e.message))) { console.error('jsdom not installed'); process.exit(2); }
  throw e;
}
const { world, press, clone, node, SM, IRBSEL, T1SEL, ROSEL } = P;

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// a 6 x 5 lattice: 30 qubits, 49 nearest-neighbour pairs, 10 gates x (SRB, IRB)
const GATES = ['cz_g0', 'cz_g1', 'cz_g2', 'cz_g3', 'cz_g4', 'cz_g5', 'cz_g6', 'cz_g7', 'cz_g8', 'cz_g9'];
// a qubit with 15 of the metric panels' metrics (15 panels x 30 qubits = 450
// cells: the metric panels are built in slices too), each varying by qubit
function bigNode(id, loc, i) {
  const n = node(id, loc), f = 1 + ((i * 7) % 13) / 100;
  Object.assign(n, { T1: 12e-6 * f, T2ramsey: 15e-6 * f, T2echo: 20e-6 * f, gate_fidelity_avg: 0.998 - i / 1e4,
    gate_fidelity_x180: 0.997 - i / 1e4, gate_fidelity_x90: 0.996 - i / 1e4, assignment_fidelity: 0.95 - i / 1e3,
    ro_fidelity_g: 0.97 - i / 1e3, ro_fidelity_e: 0.93 - i / 1e3, f_01: 5.1e9 * f, readout_frequency: 7.2e9 * f,
    anharmonicity: -200e6 * f, x180_amplitude: 0.12 * f, x90_amplitude: 0.06 * f, readout_amplitude: 0.03 * f });
  return n;
}
function bigTopo() {
  const nodes = [], edges = [];
  const id = (r, c) => 'q' + (r * 6 + c + 1);
  for (let r = 0; r < 5; r++) for (let c = 0; c < 6; c++) nodes.push(bigNode(id(r, c), c + ',' + r, r * 6 + c));
  let n = 0;
  function edge(a, b) {
    n++;
    const gf = [];
    GATES.forEach(function (g, i) {
      gf.push({ metric: 'StandardRB', gate: g, level: 'gate', value: 0.9 + ((n * 7 + i * 3) % 90) / 1000 });
      gf.push({ metric: 'InterleavedRB', gate: g, level: 'gate', value: 0.91 + ((n * 5 + i * 11) % 80) / 1000 });
    });
    edges.push({ pair_id: a + '-' + b, source: a, target: b, has_cz: true, gate_kind: 'cz', gate_fidelities: gf });
  }
  for (let r = 0; r < 5; r++) for (let c = 0; c < 6; c++) {
    if (c < 5) edge(id(r, c), id(r, c + 1));
    if (r < 4) edge(id(r, c), id(r + 1, c));
  }
  return { nodes: nodes, edges: edges };
}
const BIG = bigTopo();
const KEY = (t, g) => '2q:' + t + ':' + g;
const PSEL = (k) => '.topo-section[data-density-panel="' + k + '"]';

function built(T) {
  return Array.prototype.map.call(T.doc.querySelectorAll('#topo-2q-rb-panels .topo-section[data-density-panel]'),
                                  (e) => e.getAttribute('data-density-panel'));
}
// the section's content in page order, whatever wraps it. The keyboard
// grid's roving-tabindex marks are left out: it adds them to the cells it
// finds when it starts (a section built by then has them) and to later cells
// on the first arrow key -- as for any lazily built panel.
function section(T) {
  return Array.prototype.map.call(
    T.doc.querySelectorAll('#topo-2q-rb-panels .topo-fidelity-subtitle, #topo-2q-rb-panels [data-rb-heading], '
                           + '#topo-2q-rb-panels .topo-section[data-density-panel]'),
    (e) => {
      const c = e.cloneNode(true);
      c.querySelectorAll('[data-kbd-cell]').forEach((x) => {
        ['data-kbd-cell', 'tabindex', 'role'].forEach((a) => x.removeAttribute(a));
      });
      return c.outerHTML;
    });
}
// advance until the slices have begun (more than the press built), 5 ms at a time
async function untilSlicing(T, from) {
  for (let t = 0; t < 3000 && built(T).length <= from; t += 5) await T.advance(5);
  return built(T).length;
}
// every chart drawn, with the time it was asked for
function countCharts(win, T) {
  T.charts = [];
  win._plotlyRender = function (el) {
    T.charts.push({ id: el && el.id, at: win.Date.now() });
    return new win.Promise(function (r) { win.setTimeout(r, 300); });
  };
}
// the stored per-panel choices a reader made (tile size, Show Meta Info)
function stored(win) {
  win.localStorage.setItem('quam_chip_density_panels', JSON.stringify({ [KEY('StandardRB', 'cz_g4')]: 0.8 }));
  win.localStorage.setItem('quam_chip_meta_panels', JSON.stringify({ [KEY('InterleavedRB', 'cz_g8')]: true }));
}
// every DOM change is a layout change the dashboard's ResizeObserver sees, and
// it is delivered in the next frame before its paint (roFrame), as Chrome does
const big = (o) => world(Object.assign({ topo: BIG, roOnMutation: true, roFrame: true }, o || {}));

(async function main() {
  // the storage keys the page reads (so S3's choices are the page's own)
  {
    const fs = require('fs'), path = require('path');
    const cs = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'chip-status.js'), 'utf8');
    ok(/'quam_chip_density_panels'/.test(cs) && /'quam_chip_meta_panels'/.test(cs),
       'setup: the tile-size and Show Meta Info stores are the keys this test writes');
  }

  // ── S1 / S2 / S3 / S4: an IRB tile press ─────────────────────────────────
  let T;
  {
    T = big({ state: { htmx: true }, before: function (w) { stored(w); } });
    countCharts(T.win, T);
    await T.advance(1000);
    const before = built(T).length;
    press(T, 'mouse', 'irb');
    const now = built(T);
    ok(before === 0 && now.length >= 1 && now.length <= 2 && now[0] === KEY('InterleavedRB', 'cz_g0')
       && now.every((k) => /^2q:InterleavedRB:/.test(k))
       && !!T.doc.querySelector(IRBSEL) && !!T.doc.querySelector('[data-rb-heading="StandardRB"]'),
       'S1 the IRB press builds the IRB heading\'s first panels in the press -- a chunk, not the section: '
       + JSON.stringify(now));
    await T.advance(100);                       // the smooth scroll is part way
    const mid = built(T).length;
    await T.advance(300);                       // its last step (400 ms)
    const atEnd = built(T).length;
    ok(mid === now.length && atEnd === now.length,
       'S2 no slice runs while the jump\'s smooth scroll is in flight (' + now.length + ' -> ' + mid + ' -> ' + atEnd + ')');
    await T.advance(40);                        // its last step has run
    ok(T.topOf(IRBSEL) === SM && built(T).length === now.length,
       'S1 ...and the jump landed the IRB heading under the sticky bar, on the press\'s own panels — ' + T.topOf(IRBSEL));
    const chartsDuring = [];
    let doneAt = null;
    for (let t = 0; t < 12000 && doneAt === null; t += 5) {
      await T.advance(5);
      if (built(T).length === 20) doneAt = T.win.Date.now();
      else chartsDuring.push(T.charts.length);
    }
    ok(doneAt !== null, 'S3 every one of the 20 panels arrives (' + built(T).length + ')');
    ok(chartsDuring.length > 20 && chartsDuring.every((n) => n === 0),
       'S4 no chart is drawn while the slices run -- ' + JSON.stringify(chartsDuring.slice(-3))
       + ' done at ' + doneAt + ', charts at ' + JSON.stringify(T.charts.slice(0, 3)));
    await T.advance(20000);
    const ids = T.charts.map((c) => c.id);
    ok(ids.length === 20 && new Set(ids).size === 20 && ids[0] === 'rb-InterleavedRB-cz-g0-chart',
       'S4 ...then all 20 charts, the jump\'s target first — ' + ids.length + ' ' + ids.slice(0, 2).join(','));
    ok(T.topOf(IRBSEL) === SM,
       'S3 the IRB heading is still under the sticky bar after the 10 SRB panels above it arrived — ' + T.topOf(IRBSEL));
    // the one-shot build: F5 on a record relative to the 2Q section's top
    const R = big({ url: '/topology?view=fidelity2q', chipView: 'fidelity2q', before: function (w) { stored(w); },
                    state: { htmx: true, smChipScroll: { url: '/topology?view=fidelity2q', view: 'fidelity2q', d: 40, top: 1 } } });
    await R.advance(100);
    const a = section(T), b = section(R);
    const same = a.length === b.length && a.every((h, i) => h === b[i]);
    ok(b.length === 23 && built(R).length === 20 && same,
       'S3 ...and the section is EXACTLY the one-shot build\'s: ' + a.length + ' vs ' + b.length + ' blocks'
       + (same ? '' : ', first difference at ' + a.findIndex((h, i) => h !== b[i]) + ': ' + (function () {
         const i = a.findIndex((h, j) => h !== b[j]);
         let k = 0; while (k < a[i].length && a[i][k] === b[i][k]) k++;
         return JSON.stringify(a[i].slice(Math.max(0, k - 80), k + 80)) + ' vs ' + JSON.stringify(b[i].slice(Math.max(0, k - 80), k + 80));
       })()));
    // the slots stay, and make no box: an insertion among the host's own
    // children restyled every panel beside it (207-630 ms in real Chrome)
    const css = require('fs').readFileSync(require('path').join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'style.css'), 'utf8');
    const host = T.doc.getElementById('topo-2q-rb-panels');
    const inSlots = Array.prototype.every.call(host.querySelectorAll('.topo-section[data-density-panel]'),
      (e) => e.parentNode.classList.contains('topo-rb-slot') && e.parentNode.parentNode === host && e.parentNode.children.length === 1);
    ok(inSlots && host.querySelectorAll(':scope > .topo-rb-slot').length === 20
       && /\.topo-rb-slot\s*\{\s*display:\s*contents;\s*\}/.test(css),
       'S3 every panel went INTO its own slot, a display:contents wrapper that stays');
    const g4 = T.doc.querySelector(PSEL(KEY('StandardRB', 'cz_g4'))), g8 = T.doc.querySelector(PSEL(KEY('InterleavedRB', 'cz_g8')));
    ok(g4 && g4.style.getPropertyValue('--topo-density-scale') === '0.8' && g8 && g8.classList.contains('topo-meta-on'),
       'S3 ...with the reader\'s stored tile size (SRB cz_g4, built in a late slice) and Show Meta Info (IRB cz_g8)');
    ok(T.errors().length === 0 && R.errors().length === 0, 'S1-S4 no timer threw — ' + T.errors().concat(R.errors()).join(' | ').slice(0, 300));
  }

  // ── S5 / S6: a panel built in a later slice is wired and marked ───────────
  {
    const U = big({ state: { htmx: true }, before: function (w) {
      w.fetch = function (url) {
        if (String(url).indexOf('/state/live-diff') === 0) {
          return w.Promise.resolve({ json: function () { return w.Promise.resolve(
            { ok: true, entries: [{ dot_path: 'qubit_pairs.q2-q3.macros.cz_g6.fidelity' }] }); } });
        }
        return new w.Promise(function () {});
      };
    } });
    const calls = [];
    U.win.htmx.ajax = function (verb, url) { calls.push(verb + ' ' + url); return new U.win.Promise(function () {}); };
    await U.advance(500);
    U.win.ChipStatus.liveDiff.refresh();        // live differs on q2-q3
    await U.advance(100);
    press(U, 'mouse', 'irb');
    ok(!U.doc.querySelector(PSEL(KEY('StandardRB', 'cz_g6'))), 'S5 setup: SRB cz_g6 is not built by the press');
    await U.advance(12000);
    const cell = U.doc.querySelector(PSEL(KEY('StandardRB', 'cz_g6')) + ' .heatmap-cell[data-pair="q2-q3"]');
    ok(!!cell && cell.classList.contains('topo-changed') && /changed vs live/.test(cell.getAttribute('title') || ''),
       'S6 the changed-vs-live pair is marked in a panel built in a later slice');
    cell.dispatchEvent(new U.win.MouseEvent('click', { bubbles: true }));
    ok(calls.indexOf('GET /pair/q2-q3') >= 0, 'S5 a click on that panel\'s cell opens the pair — ' + JSON.stringify(calls));
    const others = U.doc.querySelectorAll('#topo-2q-rb-panels .heatmap-cell.topo-changed');
    ok(others.length === 20, 'S6 ...in every one of the 20 panels, once each (' + others.length + ')');
  }

  // ── S7: a T1 press -- the 2Q section is all above it ──────────────────────
  {
    const V = big({ state: { htmx: true } });
    countCharts(V.win, V);
    await V.advance(1000);
    press(V, 'mouse', 't1');
    ok(built(V).length === 0 && !!V.doc.querySelector(T1SEL),
       'S7 a T1 press builds no 2Q panel in the press (the metrics are built): ' + built(V).length);
    await V.advance(600);
    const landed = V.topOf(T1SEL);
    let doneAt = null, early = 0;
    for (let t = 0; t < 12000 && doneAt === null; t += 5) {
      await V.advance(5);
      if (built(V).length === 20) doneAt = t; else early += V.charts.length;
    }
    ok(doneAt !== null && early === 0,
       'S7 the metrics section\'s charts wait for the 2Q slices too (charts seen before the last panel: ' + early + ')');
    await V.advance(12000);
    ok(landed === SM && built(V).length === 20 && V.topOf(T1SEL) === SM,
       'S7 T1 lands under the bar and stays there while all 20 2Q panels grow above it — ' + landed + ' -> ' + V.topOf(T1SEL));
    ok(V.charts.length > 0 && V.errors().length === 0, 'S7 ...then the charts are drawn (' + V.charts.length + '), no timer threw');
  }

  // ── S8: a second jump while the slices run ───────────────────────────────
  {
    const W = big({ state: { htmx: true } });
    countCharts(W.win, W);
    await W.advance(1000);
    press(W, 'mouse', 'irb');
    await untilSlicing(W, built(W).length);     // rested: the slices run (IRB panels first)
    const mid = built(W);
    press(W, 'mouse', 'srb');
    const now = built(W);
    ok(mid.length < 20 && mid.indexOf(KEY('StandardRB', 'cz_g0')) < 0 && now.indexOf(KEY('StandardRB', 'cz_g0')) >= 0,
       'S8 an SRB press while the slices run builds the SRB heading\'s first panel in the press ('
       + mid.length + ' -> ' + now.length + ')');
    await W.advance(12000);
    ok(built(W).length === 20 && W.topOf('[data-rb-heading="StandardRB"]') === SM,
       'S8 ...lands on it, and the rest still arrive — ' + W.topOf('[data-rb-heading="StandardRB"]'));
    await W.advance(25000);
    const wids = W.charts.map((c) => c.id);
    ok(wids.length === 20 && new Set(wids).size === 20,
       'S8 ...each panel made ONCE: the builder finishes and all 20 charts are drawn, one each ('
       + wids.length + ' draws, ' + new Set(wids).size + ' charts)');
  }

  // ── S9: navigating away stops the slices ─────────────────────────────────
  {
    const X = big({ state: { htmx: true } });
    await X.advance(1000);
    press(X, 'mouse', 'irb');
    await untilSlicing(X, built(X).length);
    X.doc.body.dispatchEvent(new X.win.CustomEvent('htmx:beforeSwap', { bubbles: true,
      detail: { target: X.pane, shouldSwap: true, requestConfig: {} } }));
    const at = built(X).length;
    await X.advance(12000);
    ok(at < 20 && built(X).length === at && X.errors().length === 0,
       'S9 after the pane is swapped no slice runs (' + at + ' -> ' + built(X).length + ')');
  }

  // ── S10: F5 on a place deep inside the 2Q list ───────────────────────────
  {
    const k = KEY('StandardRB', 'cz_g7');
    const rec = { url: '/topology?view=fidelity2q', view: 'fidelity2q', d: 5000, sel: PSEL(k), ds: 120, top: 1 };
    const Y = big({ url: '/topology?view=fidelity2q', chipView: 'fidelity2q', state: { htmx: true, smChipScroll: rec } });
    ok(!!Y.doc.querySelector(PSEL(k)) && built(Y).length <= 2,
       'S10 F5 on a place inside SRB cz_g7 builds that panel at once, not the section (' + built(Y).length + ')');
    await Y.advance(20);                        // the restore's frame
    const at = Y.topOf(PSEL(k));
    await Y.advance(12000);
    ok(at === -120 && built(Y).length === 20 && Y.topOf(PSEL(k)) === -120,
       'S10 ...lands 120 px into it and is still there once the 7 panels above it arrived — ' + at + ' -> ' + Y.topOf(PSEL(k)));
  }

  // ── S11: the F5 matrix on the big chip ───────────────────────────────────
  for (const c of [['mouse', 'irb', 'fidelity2q', IRBSEL], ['Enter', 't1', 'coherence', T1SEL],
                   ['Space', 'ro_ge', 'readout', ROSEL]]) {
    for (const delay of [0, 300, 2000]) {
      const A = big({ state: { htmx: true } });
      await A.advance(3000);
      press(A, c[0], c[1]);
      await A.advance(delay);
      A.win.dispatchEvent(new A.win.Event('pagehide'));        // F5
      const st = clone(A.win.history.state), url = A.url();
      const B = big({ url: url, chipView: (url.match(/view=([^&]*)/) || [])[1] || '', state: st });
      await B.advance(15000);
      const top = B.topOf(c[3]);
      ok(url === '/topology?view=' + c[2] && top === SM && B.lit() === c[2] && built(B).length === 20
         && A.errors().length === 0 && B.errors().length === 0,
         'S11 big chip: ' + c[0] + ' -> ' + c[1] + ', F5 after ' + delay + ' ms lands on it — top ' + top
         + ' (want ' + SM + '), tab ' + B.lit() + ', panels ' + built(B).length);
    }
  }

  // ── S13 / S14: the lazy observer -- the pane reaching the section ─────────
  {
    let io = null;
    const I = big({ state: { htmx: true }, before: function (w) {
      w.IntersectionObserver = function (cb) { io = cb; this.observe = function () {}; this.disconnect = function () {}; };
    } });
    await I.advance(500);
    const host = I.doc.querySelector('[data-topo-section="2qrb"]');
    io([{ isIntersecting: true, target: host }]);        // scrolled near it
    const first = built(I);
    ok(first.length >= 1 && first.length <= 2 && first[0] === KEY('StandardRB', 'cz_g0'),
       'S14 the pane reaching the 2Q section builds its TOP panels at once, the rest in slices: ' + JSON.stringify(first));
    await I.advance(12000);
    ok(built(I).length === 20, 'S14 ...and the rest arrive (' + built(I).length + ')');
    // the readout section reached first: ITS panels first, not the 1Q ones above it
    io([{ isIntersecting: true, target: I.doc.querySelector('[data-topo-section="fidro"]') }]);
    const ro = Array.prototype.map.call(I.doc.querySelectorAll('#topo-fidelity-ro-panels .topo-section[data-density-panel], '
      + '#topo-fidelity-1q-panels .topo-section[data-density-panel], #topo-metric-panels .topo-section[data-density-panel]'),
      (e) => e.getAttribute('data-density-panel'));
    ok(ro.length >= 1 && ro.length <= 2 && ro[0] === 'assignment_fidelity' && ro.indexOf('gate_fidelity_avg') < 0,
       'S14 the pane reaching the Read. Fid. section builds the readout panels first: ' + JSON.stringify(ro));
    // a jump passing the section while its slices run
    const J2 = big({ state: { htmx: true }, before: function (w) {
      w.IntersectionObserver = function (cb) { io = cb; this.observe = function () {}; this.disconnect = function () {}; };
    } });
    await J2.advance(500);
    press(J2, 'mouse', 'irb');
    await untilSlicing(J2, built(J2).length);
    const before = built(J2);
    io([{ isIntersecting: true, target: J2.doc.querySelector('[data-topo-section="2qrb"]') }]);
    const after = built(J2);
    ok(before.length === after.length && after.indexOf(KEY('StandardRB', 'cz_g0')) < 0,
       'S13 the observer seeing the section while the slices run changes nothing (' + before.length + ' -> ' + after.length + ')');
  }

  // ── S15: a chart pump already running pauses while the slices run ─────────
  {
    let io = null;
    const K = big({ state: { htmx: true }, before: function (w) {
      w.IntersectionObserver = function (cb) { io = cb; this.observe = function () {}; this.disconnect = function () {}; };
    } });
    countCharts(K.win, K);
    await K.advance(500);
    io([{ isIntersecting: true, target: K.doc.querySelector('[data-topo-section="metrics"]') }]);   // the metrics, reached first
    // its slices are done and its first batch of charts is drawn, the rest to come
    for (let t = 0; t < 5000 && !K.charts.length; t += 5) await K.advance(5);
    const running = K.charts.length;
    press(K, 'mouse', 'irb');
    const at = K.charts.length;
    let during = 0, done = false;
    for (let t = 0; t < 12000 && !done; t += 5) {
      await K.advance(5);
      if (built(K).length === 20) done = true; else during = K.charts.length - at;
    }
    await K.advance(20000);
    ok(running > 0 && running < 15 && done && during === 0 && K.charts.length === 35,
       'S15 the metrics\' chart pump, already drawing, pauses while the 2Q slices run and goes on after ('
       + running + ' drawn, ' + during + ' during the slices, ' + K.charts.length + ' in the end)');
  }
  // ── S16: Plotly itself is not loaded while the slices run ────────────────
  {
    const loads = [];
    const L = big({ state: { htmx: true }, before: function (w) {
      w.Plotly = undefined;
      w.requirePlotly = function () { loads.push(built(L).length); w.Plotly = {}; return w.Promise.resolve(); };
    } });
    await L.advance(1000);
    press(L, 'mouse', 't1');
    await L.advance(15000);
    ok(loads.length >= 1 && loads[0] === 20,
       'S16 Plotly (a 0.6 s task of its own) is loaded only once the 2Q slices are done: panels at load ' + JSON.stringify(loads));
    // the same on a chip whose few metric panels are built in one go (8 x 30
    // cells) under a sliced 2Q section: the metric build must not start its
    // charts (or Plotly) inside the press, before the 2Q slices exist
    const small = JSON.parse(JSON.stringify(BIG));
    small.nodes = small.nodes.map((n) => node(n.id, n.grid_location));
    const loads2 = [];
    const L2 = world({ topo: small, roOnMutation: true, roFrame: true, state: { htmx: true }, before: function (w) {
      w.Plotly = undefined;
      w.requirePlotly = function () { loads2.push(built(L2).length); w.Plotly = {}; return w.Promise.resolve(); };
    } });
    await L2.advance(1000);
    press(L2, 'mouse', 't1');
    const inPress = loads2.length;
    await L2.advance(15000);
    ok(inPress === 0 && loads2.length >= 1 && loads2[0] === 20,
       'S16 ...also when the metric panels are built in one go under a sliced 2Q section: panels at load '
       + JSON.stringify(loads2) + ' (in the press: ' + inPress + ')');
  }

  // ── S17: the metric panels are sliced the same way (15 panels, 450 cells) ─
  {
    const mkeys = (T) => Array.prototype.map.call(
      T.doc.querySelectorAll('#topo-metric-panels .topo-section[data-density-panel], #topo-fidelity-1q-panels .topo-section[data-density-panel], '
                             + '#topo-fidelity-ro-panels .topo-section[data-density-panel]'),
      (e) => e.getAttribute('data-density-panel'));
    const hostsHtml = (T) => ['#topo-fidelity-1q-panels', '#topo-fidelity-ro-panels', '#topo-metric-panels'].map((h) =>
      Array.prototype.map.call(T.doc.querySelectorAll(h + ' h3[data-group], ' + h + ' .topo-section'), (e) => {
        const c = e.cloneNode(true);
        c.querySelectorAll('[data-kbd-cell]').forEach((x) => ['data-kbd-cell', 'tabindex', 'role'].forEach((a) => x.removeAttribute(a)));
        return c.outerHTML;
      }).join('\n'));
    const M = big({ state: { htmx: true }, before: function (w) { stored(w); } });
    const calls = [];
    M.win.htmx.ajax = function (verb, url) { calls.push(verb + ' ' + url); return new M.win.Promise(function () {}); };
    await M.advance(1000);
    press(M, 'mouse', 't1');
    const now = mkeys(M);
    ok(now.length >= 1 && now.length <= 2 && now[0] === 'T1' && now.indexOf('gate_fidelity_avg') < 0
       && now.indexOf('assignment_fidelity') < 0 && !!M.doc.querySelector('#topo-metric-panels [data-group="coherence"]'),
       'S17 a T1 press builds the T1 panel and the ones under it in the press, not the metric section: ' + JSON.stringify(now));
    await M.advance(12000);
    const all = mkeys(M);
    ok(all.length === 15 && M.topOf(T1SEL) === SM,
       'S17 ...all 15 metric panels arrive, T1 still under the bar with the 1Q / readout / 2Q panels grown above it — '
       + all.length + ', ' + M.topOf(T1SEL));
    const R = big({ url: '/topology?view=coherence', chipView: 'coherence', before: function (w) { stored(w); },
                    state: { htmx: true, smChipScroll: { url: '/topology?view=coherence', view: 'coherence', d: 10, top: 1 } } });
    await R.advance(100);
    const a = hostsHtml(M), b = hostsHtml(R);
    ok(mkeys(R).length === 15 && a.every((h, i) => h === b[i]) && a[2].length > 1000,
       'S17 ...and the three metric hosts are EXACTLY the one-shot build\'s (1Q, readout, the rest)'
       + (a.every((h, i) => h === b[i]) ? '' : ' -- host ' + a.findIndex((h, i) => h !== b[i]) + ' differs'));
    const cell = M.doc.querySelector('.topo-section[data-density-panel="gate_fidelity_avg"] .heatmap-cell[data-qubit="q7"]');
    if (cell) cell.dispatchEvent(new M.win.MouseEvent('click', { bubbles: true }));
    ok(!!cell && calls.indexOf('GET /qubit/q7') >= 0, 'S17 a qubit cell of a metric panel built in a later slice opens that qubit — ' + JSON.stringify(calls));
    // a press on the Read. Fid. tab builds the readout section's first panel in the press
    const N = big({ state: { htmx: true } });
    await N.advance(1000);
    press(N, 'tab', 'readout');
    const rn = mkeys(N);
    ok(rn[0] === 'assignment_fidelity' && rn.length <= 2, 'S17 a Read. Fid. tab press builds the readout section\'s top panels first: ' + JSON.stringify(rn));
    await N.advance(12000);
    ok(mkeys(N).length === 15 && built(N).length === 20 && N.topOf('#sec-readout') === SM && N.errors().length === 0,
       'S17 ...and everything arrives around it, the section still under the bar — ' + N.topOf('#sec-readout'));
    const F = big({ state: { htmx: true } });
    await F.advance(1000);
    press(F, 'tab', 'frequencies');
    const fr = mkeys(F);
    ok(fr[0] === 'f_01' && fr.length <= 2, 'S17 a Frequencies tab press builds that group\'s first panels first: ' + JSON.stringify(fr));
    // F5 on a place inside a metric panel: that panel at once, at its offset
    const E = big({ url: '/topology?view=coherence', chipView: 'coherence', state: { htmx: true,
      smChipScroll: { url: '/topology?view=coherence', view: 'coherence', d: 900, sel: '.topo-section[data-density-panel="T2echo"]', ds: 50, top: 1 } } });
    const e0 = mkeys(E);
    await E.advance(20);
    const eAt = E.topOf('.topo-section[data-density-panel="T2echo"]');
    await E.advance(12000);
    ok(e0[0] === 'T2echo' && e0.length <= 2 && eAt === -50 && mkeys(E).length === 15
       && E.topOf('.topo-section[data-density-panel="T2echo"]') === -50,
       'S17 F5 on a place inside the T2 Echo panel builds it at once and lands 50 px into it, and it stays there — '
       + JSON.stringify(e0) + ' ' + eAt + ' -> ' + E.topOf('.topo-section[data-density-panel="T2echo"]'));
  }

  // ── S18: one section slices at a time -- the jump's own first ────────────
  {
    const mcount = (T) => T.doc.querySelectorAll('#topo-metric-panels .topo-section[data-density-panel], '
      + '#topo-fidelity-1q-panels .topo-section[data-density-panel], #topo-fidelity-ro-panels .topo-section[data-density-panel]').length;
    const track = async (T, until) => {
      let pm = mcount(T), pq = built(T).length, both = 0;
      const seq = [];
      for (let t = 0; t < 15000; t += 4) {
        await T.advance(4);
        const m = mcount(T), q = built(T).length;
        if (m > pm && q > pq) both++;
        if (m > pm) seq.push('m');
        if (q > pq) seq.push('q');
        pm = m; pq = q;
        if (until(m, q)) break;
      }
      return { both: both, seq: seq.join('') };
    };
    const Q = big({ state: { htmx: true } });
    await Q.advance(1000);
    press(Q, 'mouse', 't1');
    const r = await track(Q, (m, q) => m === 15 && q === 20);
    ok(r.both === 0 && /^m+q+$/.test(r.seq),
       'S18 after a T1 press the metric section (the jump\'s own) slices first, then the 2Q section above it, never both in one frame: ' + r.seq);
    // an IRB press while the metric slices run makes the 2Q section the reader's target: it goes first now
    const Q2 = big({ state: { htmx: true } });
    await Q2.advance(1000);
    press(Q2, 'mouse', 't1');
    for (let t = 0; t < 3000 && mcount(Q2) <= 4; t += 4) await Q2.advance(4);
    const mAt = mcount(Q2);
    press(Q2, 'mouse', 'irb');
    const r2 = await track(Q2, (m, q) => m === 15 && q === 20);
    ok(mAt > 4 && mAt < 15 && r2.both === 0 && /^q+m+$/.test(r2.seq),
       'S18 an IRB press while they run puts the 2Q slices first (metric panels at the press: ' + mAt + '): ' + r2.seq);
  }

  // ── S12: a 5-qubit chain builds every panel in the press ─────────────────
  {
    const Z = world({ state: { htmx: true } });
    await Z.advance(1000);
    press(Z, 'mouse', 'irb');
    ok(built(Z).length === 2 && !Z.doc.querySelector('.topo-rb-slot'),
       'S12 a 5-qubit chain builds all of its 2Q panels in the press, no slots (' + built(Z).length + ')');
  }

  console.log(fails ? ('FAILED (' + fails + ')') : ('chip_rb_slices_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: threw ' + (e && e.stack || e)); process.exit(1); });
