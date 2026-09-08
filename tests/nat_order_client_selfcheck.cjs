/* Natural (human) numeric ordering on the CLIENT surfaces — customer rule
 * 2026-09-09: "SM 전반에서 순서에 관한 것은 반드시 이렇게 카운트되어야 한다",
 * i.e. 101 < 1009 < 1010 < 1011 and q2 < q10, never the character-by-character
 * order a plain `.sort()` / `a < b` gives.
 *
 * Every assertion drives the REAL shipped file under jsdom (no re-implemented
 * comparator is asserted on):
 *
 *   dataset-virtual.js  Datasets table sort (Qubits / Experiment columns),
 *                       the Qubits + Pairs filter pickers, the digest failed-
 *                       qubit chips, the Parameters key groups and their value
 *                       facets, and the fit-metric sort badges.
 *   chip-status.js      the overview tile settings' metric-key list.
 *   topo-graph.js       component-map feedline bus slot order (tie by label).
 *
 * Run: node tests/nat_order_client_selfcheck.cjs   (driven by
 *      tests/test_nat_order_client.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const NODE_SET_INTERVAL = global.setInterval;

let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('FAIL: ' + msg); fails++; }
}
function eqList(got, want, msg) {
  ok(JSON.stringify(got) === JSON.stringify(want),
     msg + '  [got ' + JSON.stringify(got) + ']');
}

/* ────────────────────────────────────────────────────────────────────────────
 * dataset-virtual.js
 * ──────────────────────────────────────────────────────────────────────────── */

// Ten qubits (q1..q10) + ten pairs so the plain string order and the natural
// order genuinely disagree, and a `pm` param map whose values are numbers-as-
// strings for one key (mixed with a non-number, so the key stays CATEGORICAL
// and its facets are rendered as value chips rather than a min/max range).
function mkRows() {
  const rows = [];
  const qs = ['q1', 'q2', 'q9', 'q10', 'q11'];
  // One non-number ⇒ the key stays CATEGORICAL, so its facets render as value
  // chips. '9' vs '200' is the pair the two orders genuinely disagree on
  // ('20' vs '200' would not — a prefix sorts first either way).
  const nAvg = ['1000', '200', '9', 'auto'];
  for (let i = 0; i < qs.length; i++) {
    rows.push({
      id: 100 + i, exp: 'rabi_' + (i === 0 ? 2 : i === 1 ? 10 : 3 + i),
      // ONE day, so the digest band (which summarises the latest day of the
      // filtered set) sees every row and every failed qubit.
      date: '2026-09-01', time: '0' + (i + 1) + ':00:00',
      q: [qs[i]], p: [qs[i] + '-q' + (i + 12)],
      oc: { [qs[i]]: 'failed' },      // one failure each ⇒ a COUNT TIE
      metric: '', bm: false, tags: [], status: 'successful',
      dur: 1, note: '', parent: null, hs: false,
      sm: { f_1: 1, f_10: 1, f_2: 1 },           // equal coverage ⇒ a TIE
      // ≥2 distinct values per key (the facet picker skips a single-valued
      // key) and the SAME coverage for all three ⇒ a genuine key-order tie.
      pm: { n_avg: nAvg[i % nAvg.length],
            span_1: i % 2, span_10: i % 2, span_2: i % 2 },
      f: 'f1',
    });
  }
  return rows;
}

function boot(rows) {
  const dom = new JSDOM(`<!doctype html><html><body>
      <div class="ds-digest-band"></div>
      <div class="ds-search-wrap"><input type="search" id="dataset-search"></div>
      <span id="dataset-filter-count"></span>
      <div id="sort-filter-grid">
        <div id="sort-col-badges"></div>
        <div class="sort-filter-section"><div id="sort-fit-badges"></div></div>
        <div class="sort-filter-section"><div id="sort-param-badges"></div></div>
        <div id="sort-qubit-menu"></div><span id="sort-qubit-summary"></span>
        <div id="sort-pair-menu"></div><span id="sort-pair-summary"></span>
      </div>
      <script id="ds-rows-data" data-now="1000">${JSON.stringify(rows)}</script>
      <div id="datasets-scroll" style="height:900px">
        <table>
          <colgroup id="datasets-colgroup"></colgroup>
          <thead id="datasets-thead"></thead>
          <tbody id="datasets-tbody"></tbody>
        </table>
      </div>
    </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
  const w = dom.window;
  w.requestAnimationFrame = w.requestAnimationFrame || (cb => setTimeout(cb, 0));
  w.cancelAnimationFrame = w.cancelAnimationFrame || (id => clearTimeout(id));
  global.window = w;
  global.CSS = w.CSS;
  global.document = w.document;
  global.Event = w.Event;
  global.CustomEvent = w.CustomEvent;
  global.KeyboardEvent = w.KeyboardEvent;
  global.MouseEvent = w.MouseEvent;
  global.requestAnimationFrame = w.requestAnimationFrame;
  global.cancelAnimationFrame = w.cancelAnimationFrame;
  global.localStorage = w.localStorage;
  const rec = function (fn, ms) { return NODE_SET_INTERVAL(fn, ms); };
  w.setInterval = rec; global.setInterval = rec;
  const stub = () => new Promise(() => {});
  w.fetch = stub; global.fetch = stub;
  w.htmx = { ajax: () => Promise.resolve() }; global.htmx = w.htmx;
  w.eval(fs.readFileSync(path.join(STATIC, 'dataset-virtual.js'), 'utf8'));
  w.DatasetVirtual.init();
  return w;
}

function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }

function clickHeader(w, colKey) {
  const th = w.document.querySelector('#datasets-thead th[data-col-key="' + colKey + '"]');
  if (!th) return false;
  th.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  return true;
}
function renderedCol(w, colKey) {
  return Array.prototype.map.call(
    w.document.querySelectorAll('#datasets-tbody tr[data-id]'),
    tr => {
      const td = tr.querySelector('td[data-col-key="' + colKey + '"]');
      return td ? td.textContent.trim() : '';
    });
}

(async () => {
  // ── the Datasets table, sorted by a STRING column ────────────────────────
  {
    const w = boot(mkRows());
    await tick();
    ok(clickHeader(w, 'qubits'), 'the Qubits column header is present + clickable');
    await tick();
    let col = renderedCol(w, 'qubits');
    // a th click on a `str` column starts ASCENDING (data-type != "num")
    eqList(col, ['q1-q12', 'q2-q13', 'q9-q14', 'q10-q15', 'q11-q16'],
           'Qubits column ascending is q1 … q9, q10, q11 (not q1, q10, q11, q2, q9)');
    clickHeader(w, 'qubits');
    await tick();
    eqList(renderedCol(w, 'qubits').slice(),
           ['q11-q16', 'q10-q15', 'q9-q14', 'q2-q13', 'q1-q12'],
           'and descending is the exact reverse');

    clickHeader(w, 'exp');
    await tick();
    eqList(renderedCol(w, 'exp'),
           ['rabi_2', 'rabi_5', 'rabi_6', 'rabi_7', 'rabi_10'],
           'Experiment column: rabi_2 … rabi_10 (a plain compare put rabi_10 first)');
  }

  // ── the Qubits / Pairs filter pickers ────────────────────────────────────
  {
    const w = boot(mkRows());
    await tick();
    const qs = Array.prototype.map.call(
      w.document.querySelectorAll('#sort-qubit-menu input[data-qubit]'),
      cb => cb.getAttribute('data-qubit'));
    eqList(qs, ['q1', 'q2', 'q9', 'q10', 'q11'],
           'Qubits picker lists q1, q2, q9, q10, q11');
    const ps = Array.prototype.map.call(
      w.document.querySelectorAll('#sort-pair-menu input[data-pair]'),
      cb => cb.getAttribute('data-pair'));
    eqList(ps, ['q1-q12', 'q2-q13', 'q9-q14', 'q10-q15', 'q11-q16'],
           'Pairs picker lists q1-q12 … q11-q16 (was q1-q12, q10-q15, q11-q16, …)');
  }

  // ── the digest band's failed-qubit chips (count tie → natural order) ─────
  {
    const w = boot(mkRows());
    await tick();
    const s = w.document.getElementById('dataset-search');
    s.value = 'rabi';                       // force the FILTERED digest to render
    s.dispatchEvent(new w.Event('input', { bubbles: true }));
    await tick(300);
    const chips = Array.prototype.map.call(
      w.document.querySelectorAll('.ds-digest-band .ds-digest-qchip'),
      b => b.getAttribute('data-example'));
    eqList(chips,
           ['qubit:q1 outcome:fail', 'qubit:q2 outcome:fail', 'qubit:q9 outcome:fail',
            'qubit:q10 outcome:fail', 'qubit:q11 outcome:fail'],
           'digest failed-qubit chips break a count tie in natural qubit order');
  }

  // ── the Sort banner: fit badges, param key groups, param value facets ────
  {
    const w = boot(mkRows());
    await tick();
    const fitKeys = Array.prototype.map.call(
      w.document.querySelectorAll('#sort-fit-badges [data-sort-fit="1"]'),
      b => b.getAttribute('data-sort-key'));
    eqList(fitKeys, ['f_1', 'f_2', 'f_10'],
           'fit-metric badges break a coverage tie as f_1, f_2, f_10');

    const pKeys = Array.prototype.map.call(
      w.document.querySelectorAll('#sort-param-badges [data-param-group]'),
      b => b.getAttribute('data-param-group'));
    eqList(pKeys, ['n_avg', 'span_1', 'span_2', 'span_10'],
           'Parameters groups break a coverage tie as span_1, span_2, span_10');

    // expand n_avg → its value facets are STRINGS ("1000", "200", "20", "auto")
    const head = w.document.querySelector('[data-param-group="n_avg"]');
    head.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
    await tick();
    const vals = Array.prototype.map.call(
      w.document.querySelectorAll('[data-param-key="n_avg"]'),
      b => b.getAttribute('data-param-val'));
    // "1000" appears twice in the 5 rows (i%4 cycles), so it leads on count;
    // the remaining three are a 1-each tie broken naturally: 9 < 200 < auto.
    eqList(vals, ['1000', '9', '200', 'auto'],
           'n_avg value facets order the tie 9 < 200 (a plain compare said 200 < 9)');
  }

  // ── chip-status.js: the "add panel" metric list (x90 before x180) ────────
  {
    const dom = new JSDOM(
      '<!DOCTYPE html><html><body>'
      + '<div id="topo-hero"></div><div id="topo-html-wrap"></div>'
      + '<h3>Overview <button id="ov-settings-btn"></button>'
      + '<span id="ov-custom-note" hidden></span></h3>'
      + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
      + '</body></html>',
      { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const w = dom.window;
    w.htmx = { ajax: function () {} };
    w.fetch = function () { return new w.Promise(function () {}); };
    new w.Function(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8') + '\n;\n'
                 + fs.readFileSync(path.join(STATIC, 'topo-graph.js'), 'utf8') + '\n;\n'
                 + fs.readFileSync(path.join(STATIC, 'chip-status.js'), 'utf8')).call(w);
    // The real METRIC_META carries x180_amplitude AND x90_amplitude — the pair
    // a character-by-character sort gets backwards (…180… before …90…).
    function node(id, gl, t1) {
      return { id: id, grid_location: gl, T1: t1, gate_fidelity_avg: 0.999,
               metrics: { T1: { value: t1 }, gate_fidelity_avg: { value: 0.999 },
                          x180_amplitude: { value: 0.1 }, x90_amplitude: { value: 0.05 } } };
    }
    w.ChipStatus.mount({ topo: { nodes: [node('q1', '0,0', 1e-5), node('q2', '1,0', 2e-5)],
                                 edges: [] },
                         rawWiring: {}, defaultThresholds: {}, diagFindings: [],
                         metricMeta: {} });
    w.document.getElementById('ov-add-tile')
      .dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    const sel = w.document.getElementById('ov-tile-popover').querySelector('#ov-pop-key');
    const keys = Array.prototype.map.call(sel.options, o => o.value);
    eqList(keys, ['gate_fidelity_avg', 'T1', 'x90_amplitude', 'x180_amplitude'],
           'chip-status add-panel metric list puts x90_amplitude before x180_amplitude');
  }

  // ── generate.js: the step-5 wiring error names the qubits it found ───────
  {
    const HTML = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web',
                                           'templates', '_generate.html'), 'utf8');
    const dom = new JSDOM(
      '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
      { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const w = dom.window;
    w.NumberInput = { fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} },
                      format() {}, strip(s) { return String(s == null ? '' : s); } };
    w.armPlainResize = function () {};
    w.renderInstrumentWiring = function () {};
    w.confirm = function () { return true; };
    w.fetch = function () { return new w.Promise(function () {}); };
    new w.Function(fs.readFileSync(path.join(STATIC, 'generate.js'), 'utf8')).call(w);
    const T = w.QuamGen._test;
    // Five qubits on ONE readout output port whose INPUT ports disagree ⇒ the
    // R1 error, whose message lists every qubit on the offending bundle.
    const alloc = {};
    ['q1', 'q2', 'q9', 'q10', 'q11'].forEach(function (q, i) {
      alloc[q] = { rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' },
                        { con: 1, slot: 1, port: 1 + (i % 2), io_type: 'input' }] };
    });
    T.state.allocation = alloc;
    const msg = (T.validateWiring().filter(it => it.level === 'error')[0] || {}).message || '';
    const listed = (msg.match(/carries ([^b]*?) but /) || [])[1] || '';
    ok(listed.trim() === 'q1, q2, q9, q10, q11',
       'the wiring error lists q1, q2, q9, q10, q11 (was q1, q10, q11, q2, q9)'
       + '  [got "' + listed.trim() + '"]');
    T.state.allocation = null;
  }

  // ── topo-graph.js: feedline bus slot order, tie on the label ─────────────
  {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
                          { runScripts: 'outside-only', pretendToBeVisual: true,
                            url: 'http://localhost/' });
    const w = dom.window;
    new w.Function(fs.readFileSync(path.join(STATIC, 'topo-graph.js'), 'utf8')).call(w);
    // Every resonator on ONE row ⇒ every bus has the SAME mean along the
    // dominant axis, so the label is the only tiebreak — and the tiebreak is
    // what picks each bus's colour slot.
    const nodes = [];
    ['line_1', 'line_2', 'line_10'].forEach(function (fl, i) {
      nodes.push({ id: 'q' + (2 * i + 1), grid_location: (2 * i) + ',0', rr_port: fl });
      nodes.push({ id: 'q' + (2 * i + 2), grid_location: (2 * i + 1) + ',0', rr_port: fl });
    });
    const m = w.document.createElement('div');
    w.document.body.appendChild(m);
    const api = w.TopoGraph.renderLayout(m, { nodes: nodes, edges: [], highlight: 'resonators' });
    eqList((api.feeds || []).map(f => f.label + '@' + f.slot),
           ['line_1@1', 'line_2@2', 'line_10@3'],
           'component-map feedline buses take colour slots in natural label order');
  }

  process.exit(fails ? 1 : 0);
})();
