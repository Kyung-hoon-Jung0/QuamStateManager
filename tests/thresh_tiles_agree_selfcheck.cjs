/* QA chipstatus-r2-07 — the two in-spec tiles on one page must agree.
 *
 * Measured in real Chrome: set T1 warn 40 / fail 20 in the threshold editor
 * and press "Update colour bands". The Health tile re-scored ("4 failing ·
 * 1 warn") while the Overview "Qubits In Spec" tile kept the count from the
 * bands the page was RENDERED with ("2 warn · 3 fail") until a reload. The
 * ↺ reset did the same the other way round. Both tiles walk the same
 * verdicts; only buildHealthSummary() was re-run by the threshold handlers.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom, a fake
 * /chip-status/spec that merges like core/spec_thresholds.save): after each
 * threshold path -- Apply, reset-one, reset-all, a refused save that reverts,
 * and another window's bands adopted on re-open -- the Overview tile's
 * "N warn · M fail" equals the Health tile's below-spec counts, and both
 * equal the count recomputed here from the bands in force.
 *
 * Run: node tests/thresh_tiles_agree_selfcheck.cjs
 *      (driven by tests/test_spec_thresholds.py)
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

const DEFAULTS = {
  T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' },
};
// Five qubits whose T1 verdicts move with the bands: under the defaults
// 2 warn + 1 fail; under warn 40 / fail 20 µs, 2 warn + 2 fail.
const T1S = [5.0e-5, 3.5e-5, 2.5e-5, 1.5e-5, 0.5e-5];
const topo = {
  nodes: T1S.map(function (t, i) {
    return { id: 'q' + (i + 1), grid_location: i + ',0', T1: t, metrics: { T1: { value: t } } };
  }),
  edges: [], summary: {},
};

function makeServer() {
  const srv = { stored: {}, mode: 'ok' };
  srv.spec = function () {
    const metrics = JSON.parse(JSON.stringify(DEFAULTS));
    const edited = [];
    Object.keys(srv.stored).forEach(function (k) {
      Object.keys(srv.stored[k]).forEach(function (b) { metrics[k][b] = srv.stored[k][b]; });
      edited.push(k);
    });
    return { metrics: metrics, edited: edited, source: edited.length ? 'lab' : 'default',
             summary: edited.length ? 'your lab\'s bands' : 'SM\'s own default bands' };
  };
  srv.fetch = function (url, opts) {
    const u = String(url).split('?')[0];
    const method = (opts && opts.method) || 'GET';
    if (!/^\/chip-status\/spec/.test(u)) return new Promise(function () {});
    if (method === 'GET') {
      const body = srv.spec();
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
    }
    if (srv.mode === 'reject') return Promise.reject(new TypeError('Failed to fetch'));
    if (u === '/chip-status/spec/clear') srv.stored = {};
    else {
      const posted = JSON.parse(opts.body.get('metrics'));
      Object.keys(posted).forEach(function (k) {
        const st = Object.assign({}, srv.stored[k] || {});
        ['warn', 'fail'].forEach(function (b) {
          const v = posted[k][b];
          if (typeof v !== 'number') return;
          if (Math.abs(v - DEFAULTS[k][b]) > 1e-12) st[b] = v; else delete st[b];
        });
        if (Object.keys(st).length) srv.stored[k] = st; else delete srv.stored[k];
      });
    }
    const body = { ok: true, spec: srv.spec() };
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
  };
  return srv;
}

function world(srv) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane"><div class="topo-dashboard">'
    + '<div id="topo-hero"></div><div id="topo-overview-tiles"></div>'
    + '<div id="topo-health-tiles"></div>'
    + '<div id="topo-thresh-editor" hidden></div>'
    + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology' });
  const win = dom.window;
  win.UI_CONFIG = {};
  win.htmx = { process: function () {}, ajax: function () {} };
  win.fetch = srv.fetch;
  new win.Function(SRC).call(win);
  win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(topo)), rawWiring: {}, diagFindings: [],
                         metricMeta: {}, defaultThresholds: JSON.parse(JSON.stringify(DEFAULTS)),
                         labSpec: srv.spec() });
  return win;
}
function field(win, k, b) {
  return win.document.querySelector('#topo-thresh-editor .thresh-in[data-metric="' + k + '"][data-bound="' + b + '"]');
}
function type(win, k, b, v) {
  const f = field(win, k, b);
  f.value = String(v);
  f.dispatchEvent(new win.Event('input', { bubbles: true }));
}
function settle() { return new Promise(function (r) { setTimeout(r, 30); }); }

// The Overview "Qubits In Spec" tile's own words: "W warn · F fail".
function overview(win) {
  const host = win.document.getElementById('topo-overview-tiles');
  const tiles = host ? Array.prototype.slice.call(host.children) : [];
  const t = tiles.filter(function (el) { return /Qubits In Spec/i.test(el.textContent); })[0];
  const sub = t && t.querySelector('.topo-card-sub');
  const m = sub && /(\d+)\s*warn\s*·\s*(\d+)\s*fail/.exec(sub.textContent);
  return m ? { warn: +m[1], fail: +m[2], text: t.textContent.replace(/\s+/g, ' ').trim() }
           : { warn: NaN, fail: NaN, text: t ? t.textContent : '(no tile)' };
}
// The Health "qubits below spec" tile: value = below count, sub names the fails.
function health(win) {
  const tiles = win.document.querySelectorAll('#topo-health-tiles .topo-health-tile');
  for (let i = 0; i < tiles.length; i++) {
    const lab = tiles[i].querySelector('.tile-label');
    if (!lab || lab.textContent !== 'qubits below spec') continue;
    const below = parseInt(tiles[i].querySelector('.tile-val').textContent, 10);
    const sub = (tiles[i].querySelector('.tile-sub') || {}).textContent || '';
    const m = /(\d+)\s*failing/.exec(sub);
    const fail = m ? +m[1] : 0;
    return { warn: below - fail, fail: fail, text: below + ' | ' + sub };
  }
  return { warn: NaN, fail: NaN, text: '(no tile)' };
}
function expected(warnB, failB) {
  let w = 0, f = 0;
  T1S.forEach(function (t) { if (t < failB) f++; else if (t < warnB) w++; });
  return { warn: w, fail: f };
}
function agree(win, label, exp) {
  const o = overview(win), h = health(win);
  ok(o.warn === h.warn && o.fail === h.fail,
    label + ': Overview and Health agree — ov "' + o.text + '" vs health "' + h.text + '"');
  ok(o.warn === exp.warn && o.fail === exp.fail,
    label + ': …on the count under the bands in force — want ' + exp.warn + ' warn · '
    + exp.fail + ' fail, ov has ' + o.warn + ' warn · ' + o.fail + ' fail');
}

(async function () {
  const DEF = expected(3e-5, 1e-5);
  const EDIT = expected(4e-5, 2e-5);
  ok(DEF.warn !== EDIT.warn || DEF.fail !== EDIT.fail,
    'fixture: the two band sets really give different counts (else every pin is vacuous)');
  {
    const srv = makeServer();
    const win = world(srv);
    agree(win, 'A0 at mount', DEF);

    win.toggleThresholdEditor();
    await settle();
    type(win, 'T1', 'warn', '40');
    type(win, 'T1', 'fail', '20');
    win.applyThresholds();
    agree(win, 'A1 on pressing Apply (before the server answers)', EDIT);
    await settle();
    agree(win, 'A2 after the server stored it', EDIT);

    win.resetMetricThreshold('T1');
    await settle();
    agree(win, 'B1 after ↺ reset of the T1 row', DEF);

    type(win, 'T1', 'warn', '40');
    type(win, 'T1', 'fail', '20');
    win.applyThresholds();
    await settle();
    agree(win, 'B2 applied again', EDIT);
    win.resetThresholds();
    await settle();
    agree(win, 'B3 after reset-all', DEF);

    srv.mode = 'reject';
    type(win, 'T1', 'warn', '40');
    type(win, 'T1', 'fail', '20');
    win.applyThresholds();
    agree(win, 'C1 Apply pressed while the server is down (optimistic)', EDIT);
    await settle();
    agree(win, 'C2 …and after the refused save reverted to the saved bands', DEF);
  }
  {
    // another window saved warn 40 / fail 20 after this page rendered
    const srv = makeServer();
    const win = world(srv);
    srv.stored = { T1: { warn: 4e-5, fail: 2e-5 } };
    win.toggleThresholdEditor();
    await settle();
    agree(win, 'D1 another window\'s bands adopted on opening the editor', EDIT);
  }

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('thresh_tiles_agree_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
