/* Behavioral check for the Trends column control + point provenance against
 * the REAL shipped chip-status.js (window.ChipTrends) under jsdom.
 *
 * Customer feedback 2026-09-09, two asks:
 *   1) one column by default, with a (Columns: 1 2 3) badge control beside the
 *      section title;
 *   2) a point's hover names the run it came from, and a click on a point that
 *      came from a run opens that run's dataset panel.
 *
 * Pins:
 *  - setCols writes --trends-cols on the grid, moves .active/aria-pressed to
 *    the pressed badge, and persists the choice;
 *  - render() RE-APPLIES the stored value (the section is re-fetched on every
 *    metric toggle, so a value applied once would otherwise be lost) and falls
 *    back to 1 on junk / unreadable storage;
 *  - a click on a point whose snapshot carries a uid calls htmx.ajax with
 *    "/dataset/<folder_key>:<run_id>" into #table-pane;
 *  - a click on a point with NO uid does nothing at all;
 *  - the hover line names "#<run> · <short>" for a run and the why-sentence
 *    otherwise, with the click hint only where a uid exists;
 *  - no snapshot map (absent or unparseable) => the pre-2026-09-09 behaviour,
 *    byte-for-byte: 2-tuple customdata, no click binding.
 *
 * Run: node tests/trends_provenance_selfcheck.cjs   (driven by
 *      tests/test_trends_provenance.py)
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

const ROOT = path.join(__dirname, '..');
const read = (f) => fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', f), 'utf8');
const APP_JS = read('app.js');
const TOPO_JS = read('topo-graph.js');
const CS_JS = read('chip-status.js');

let checks = 0;
let fails = 0;
function ok(c, m) { checks++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const CHARTS = [{
  metric: 'f_01', unit: 'Hz',
  series: [{ entity: 'qA1', points: [['20260901_010000', 6.0e9],
                                     ['20260901_010100', 6.1e9],
                                     ['20260901_010200', 6.2e9]] }],
}];
const SNAPS = {
  '20260901_010000': { run: null, node: '', short: '',
                       why: 'Modified externally', uid: null },
  '20260901_010100': { run: 34, node: '03_resonator_spectroscopy_single',
                       short: '03 Res spec', why: null, uid: 'a1b2c3d4:34' },
  '20260901_010200': { run: 99, node: '06_ramsey', short: '06 Ramsey',
                       why: null, uid: null },
};

/* One grid, one badge row, one chart host — the shape _wiring.html +
   _topo_trends.html actually render. `snapsText` null omits the map element
   entirely (the degrade case). */
function makeWorld(opts) {
  opts = opts || {};
  const snapsText = ('snapsText' in opts) ? opts.snapsText : JSON.stringify(SNAPS);
  const html =
      '<!DOCTYPE html><html><body>'
    + '<div class="topo-section" data-topo-section="trends">'
    + '<div class="topo-trends-head"><h3 class="topo-section-title">Trends</h3>'
    + '<span class="topo-trends-cols">'
    + '<button type="button" class="topo-trend-chip active" data-trend-cols="1" aria-pressed="true">1</button>'
    + '<button type="button" class="topo-trend-chip" data-trend-cols="2" aria-pressed="false">2</button>'
    + '<button type="button" class="topo-trend-chip" data-trend-cols="3" aria-pressed="false">3</button>'
    + '</span></div>'
    + '<div id="topo-trends" class="topo-trends">'
    + '<div class="topo-trends-grid">'
    + '<div class="topo-trend-box" data-trend-metric="f_01">'
    + '<div class="topo-trend-chart" id="topo-trend-0"></div></div>'
    + '</div>'
    + '<script type="application/json" id="topo-trends-data"></script>'
    + (snapsText === null ? ''
       : '<script type="application/json" id="topo-trends-snaps"></script>')
    + '</div></div>'
    + '<div id="table-pane"></div>'
    + '</body></html>';
  const dom = new JSDOM(html, { runScripts: 'outside-only',
                                pretendToBeVisual: true,
                                url: 'http://localhost/' });
  const win = dom.window;
  win.document.getElementById('topo-trends-data').textContent = JSON.stringify(CHARTS);
  if (snapsText !== null) {
    win.document.getElementById('topo-trends-snaps').textContent = snapsText;
  }
  win._htmxCalls = [];
  win.htmx = { ajax: function () {
    win._htmxCalls.push(Array.prototype.slice.call(arguments));
    return win.Promise.resolve();
  } };
  win.fetch = function () { return new win.Promise(function () {}); };
  win._resized = [];
  // A fake Plotly: records the traces it was handed and gives the host the
  // EventEmitter surface the real one does, so the click binding is exercised
  // rather than mocked away.
  win.Plotly = {
    newPlot: function (el, data, layout, cfg) {
      el.data = data; el.layout = layout; el._fullLayout = layout;
      el.__renders = (el.__renders || 0) + 1;
      el.__handlers = el.__handlers || {};
      el.on = function (name, fn) {
        (el.__handlers[name] = el.__handlers[name] || []).push(fn);
      };
      el.removeAllListeners = function (name) {
        if (el.__handlers) el.__handlers[name] = [];
      };
      return win.Promise.resolve(el);
    },
    react: function (el, d, l, c) { return win.Plotly.newPlot(el, d, l, c); },
    Plots: { resize: function (el) { win._resized.push(el.id || 'anon'); } },
  };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  // requirePlotly would inject a <script> jsdom never runs; hand it ours.
  win.requirePlotly = function () { return win.Promise.resolve(win.Plotly); };
  return win;
}

function render(win) {
  win.ChipTrends.render(JSON.parse(
    win.document.getElementById('topo-trends-data').textContent));
}
function grid(win) { return win.document.querySelector('.topo-trends-grid'); }
function badge(win, n) {
  return win.document.querySelector('[data-trend-cols="' + n + '"]');
}
function host(win) { return win.document.getElementById('topo-trend-0'); }
function fire(win, name, evt) {
  const hs = (host(win).__handlers || {})[name] || [];
  hs.forEach(function (fn) { fn(evt); });
  return hs.length;
}
/* Settle on a CONDITION, never on a wall clock (the standing harness rule):
   _plotlyRender chains requirePlotly + newPlot and ChipTrends binds the click
   on the far side of that promise, so the number of ticks is an implementation
   detail nobody should have to guess. */
function until(win, cond, label) {
  return new win.Promise(function (resolve, reject) {
    var n = 0;
    (function step() {
      var got = false;
      try { got = !!cond(); } catch (e) { got = false; }
      if (got) return resolve();
      if (++n > 400) return reject(new Error('never settled: ' + label));
      win.setTimeout(step, 0);
    })();
  });
}
function drawn(win, nth) {
  return until(win, function () { return host(win).__renders === (nth || 1); },
               'render #' + (nth || 1));
}
function bound(win, nth) {
  return drawn(win, nth).then(function () {
    return until(win, function () {
      return ((host(win).__handlers || {}).plotly_click || []).length === 1;
    }, 'click binding after render #' + (nth || 1));
  });
}

const world = [];

// ── 1) setCols writes the var, marks the badge, persists ────────────────────
{
  const win = makeWorld();
  win.ChipTrends.setCols(3);
  ok(grid(win).style.getPropertyValue('--trends-cols') === '3',
     '1a setCols(3) writes --trends-cols=3 on the grid');
  ok(badge(win, 3).classList.contains('active')
     && badge(win, 3).getAttribute('aria-pressed') === 'true',
     '1b the pressed badge is active');
  ok(!badge(win, 1).classList.contains('active')
     && badge(win, 1).getAttribute('aria-pressed') === 'false',
     '1c the previous badge is released');
  ok(win.localStorage.getItem('quam_trends_cols') === '3',
     '1d the choice is persisted');
  win.ChipTrends.setCols(2);
  ok(grid(win).style.getPropertyValue('--trends-cols') === '2'
     && win.localStorage.getItem('quam_trends_cols') === '2',
     '1e a second press moves it again');
  // Out of range clamps to 1 rather than writing nonsense into the grid.
  win.ChipTrends.setCols(9);
  ok(grid(win).style.getPropertyValue('--trends-cols') === '1'
     && badge(win, 1).classList.contains('active'),
     '1f an out-of-range value clamps to one column');
}

// ── 1b) the resize is deferred one frame ───────────────────────────────────
/* Resizing inside the press lands in the same frame as PlotHost's own
   ResizeObserver callback (the grid just changed width) and Chrome logs
   "ResizeObserver loop completed with undelivered notifications" — measured in
   real headless Chrome, and gone after the rAF. */
world.push((function () {
  const win = makeWorld();
  render(win);
  return drawn(win).then(function () {
    win._resized.length = 0;
    win.ChipTrends.setCols(2);
    ok(win._resized.length === 0,
       '1g setCols does NOT resize synchronously inside the press');
    // A bounded wait that RESOLVES either way, so the assert below names the
    // failure instead of the harness dying on an unhandled rejection.
    return until(win, function () { return win._resized.length > 0; },
                 'deferred resize').catch(function () {});
  }).then(function () {
    ok(win._resized.indexOf('topo-trend-0') >= 0,
       '1h ...but the chart is resized on the next frame');
  });
})());

// ── 2) render() re-applies the stored value; junk falls back to 1 ───────────
{
  const win = makeWorld();
  win.localStorage.setItem('quam_trends_cols', '2');
  render(win);
  ok(grid(win).style.getPropertyValue('--trends-cols') === '2',
     '2a render re-applies the stored value (the fragment is re-swapped)');
  ok(badge(win, 2).classList.contains('active'),
     '2b ...and the badge follows it');

  const junk = makeWorld();
  junk.localStorage.setItem('quam_trends_cols', 'banana');
  render(junk);
  ok(grid(junk).style.getPropertyValue('--trends-cols') === '1',
     '2c junk in storage falls back to one column');

  // A private window throws on ACCESS, not just on write. Restored right after
  // the (synchronous) column pass so app.js's own later storage readers, which
  // this harness is not testing, still work.
  const priv = makeWorld();
  const real = priv.localStorage;
  Object.defineProperty(priv, 'localStorage', {
    get: function () { throw new Error('SecurityError'); }, configurable: true });
  let threw = false;
  try { render(priv); } catch (e) { threw = true; }
  Object.defineProperty(priv, 'localStorage',
                        { value: real, configurable: true, writable: true });
  ok(!threw, '2d unreadable storage never breaks the render');
  ok(grid(priv).style.getPropertyValue('--trends-cols') === '1',
     '2e ...and falls back to one column');
}

// ── 3) the hover line and the customdata join ──────────────────────────────
world.push((function () {
  const win = makeWorld();
  render(win);
  return drawn(win).then(function () {
    const tr = host(win).data[0];
    ok(tr.customdata.length === 3 && tr.customdata[0].length === 3,
       '3a customdata is joined in the browser: [id, provenance, hint]');
    ok(tr.customdata[0][1] === 'Modified externally',
       '3b a no-run point states WHY, in the customer\'s own words');
    ok(tr.customdata[0][2] === '',
       '3c ...and offers no click hint');
    ok(tr.customdata[1][1] === '#34 · 03 Res spec',
       '3d a run point names the run and the NUMBERED node name');
    ok(/click to open the dataset/.test(tr.customdata[1][2]),
       '3e ...with the click hint, because it has a uid');
    ok(tr.customdata[2][1] === '#99 · 06 Ramsey' && tr.customdata[2][2] === '',
       '3f a run whose folder is not a dataset root keeps its number, loses the click');
    ok(/%\{customdata\[0\]\}/.test(tr.hovertemplate)
       && /%\{customdata\[1\]\}/.test(tr.hovertemplate),
       '3g the snapshot id stays in the hover beside the provenance line');
  });
})());

// ── 4) the click ───────────────────────────────────────────────────────────
world.push((function () {
  const win = makeWorld();
  render(win);
  return bound(win).then(function () {
    const pts = host(win).data[0].customdata;
    ok(fire(win, 'plotly_click', { points: [{ customdata: pts[1] }] }) === 1,
       '4a exactly one click handler is bound');
    ok(win._htmxCalls.length === 1, '4b a uid point issues one request');
    ok(win._htmxCalls[0][0] === 'GET' && win._htmxCalls[0][1] === '/dataset/a1b2c3d4:34',
       '4c ...to /dataset/<folder_key>:<run_id>, never a bare run id');
    ok(win._htmxCalls[0][2].target === '#table-pane',
       '4d ...into the main pane');

    win._htmxCalls.length = 0;
    fire(win, 'plotly_click', { points: [{ customdata: pts[0] }] });
    fire(win, 'plotly_click', { points: [{ customdata: pts[2] }] });
    ok(win._htmxCalls.length === 0,
       '4e a point with no uid does NOTHING — no navigation, no 404');

    // Guards: a chart must survive nonsense from Plotly's own callback.
    let threw = false;
    try {
      fire(win, 'plotly_click', {});
      fire(win, 'plotly_click', { points: [] });
      fire(win, 'plotly_click', { points: [{ customdata: null }] });
      fire(win, 'plotly_click', { points: [{ customdata: ['20260101_000000', '', ''] }] });
    } catch (e) { threw = true; }
    ok(!threw && win._htmxCalls.length === 0,
       '4f an empty/unknown click is swallowed, not thrown into Plotly');

    // Cursor follows clickability, not merely "there was a run".
    fire(win, 'plotly_hover', { points: [{ customdata: pts[1] }] });
    ok(host(win).style.cursor === 'pointer', '4g the cursor says clickable');
    fire(win, 'plotly_hover', { points: [{ customdata: pts[2] }] });
    ok(host(win).style.cursor !== 'pointer',
       '4h ...and stays plain on a point that would not open');

    // A second render must not stack a second handler.
    render(win);
    return drawn(win, 2).then(function () {
      return until(win, function () {
        return ((host(win).__handlers || {}).plotly_click || []).length > 0;
      }, 'rebound after render #2');
    }).then(function () {
      ok(((host(win).__handlers || {}).plotly_click || []).length === 1,
         '4i re-rendering does not stack a second click handler');
      win._htmxCalls.length = 0;
      fire(win, 'plotly_click', { points: [{ customdata: host(win).data[0].customdata[1] }] });
      ok(win._htmxCalls.length === 1, '4j ...so one press is still one request');
    });
  });
})());

// ── 5) no map => exactly the old behaviour ─────────────────────────────────
world.push((function () {
  const none = makeWorld({ snapsText: null });
  render(none);
  const bad = makeWorld({ snapsText: '{not json' });
  render(bad);
  return drawn(none).then(function () { return drawn(bad); }).then(function () {
    [['5a', none, 'no map element'], ['5b', bad, 'an unparseable map']].forEach(
      function (row) {
        const tag = row[0], win = row[1], what = row[2];
        const tr = host(win).data[0];
        ok(typeof tr.customdata[0] === 'string',
           tag + ' with ' + what + ' the customdata stays the bare snapshot id');
        ok(!/customdata\[/.test(tr.hovertemplate),
           tag + "' the hovertemplate stays the pre-2026-09-09 one");
        ok(((host(win).__handlers || {}).plotly_click || []).length === 0,
           tag + '" nothing is clickable');
      });
  });
})());

// ── 6) the Param History drawer's click, same one spelling ─────────────────
/* The drawer built "/dataset/" + runId from the BARE run id, which
   `_split_dataset_uid` refuses, so it has always landed on the not-found
   panel. It reads the server-minted uid now — and offers the click only where
   there is one. This drives the real app.js function. */
world.push((function () {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="param-history-drawer">'
    + '<div id="phd-chart"></div></div><div id="table-pane"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._htmxCalls = [];
  win.htmx = { ajax: function () {
    win._htmxCalls.push(Array.prototype.slice.call(arguments)); } };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.Plotly = {
    newPlot: function (id, data, layout) {
      const el = win.document.getElementById(id);
      el.data = data; el.__renders = (el.__renders || 0) + 1;
      el.__handlers = {};
      el.on = function (n, fn) { (el.__handlers[n] = el.__handlers[n] || []).push(fn); };
      return win.Promise.resolve(el);
    },
  };
  // win.eval, not new win.Function: a TOP-LEVEL app.js function declaration is
  // a window property in a browser but a local of the wrapper function in the
  // Node realm (the docs/146 harness lesson), and this one is called by name.
  win.eval(APP_JS);
  win.requirePlotly = function () { return win.Promise.resolve(win.Plotly); };
  const chart = win.document.getElementById('phd-chart');
  win.paramHistoryRenderDrawerChart({
    property: 'f_01',
    values: [
      { timestamp: '20260901_010000', value: 6.0e9, trigger: 'manual',
        run_id: null, experiment: null, uid: null },
      { timestamp: '20260901_010100', value: 6.1e9, trigger: 'experiment',
        run_id: 34, experiment: '06_ramsey', uid: 'a1b2c3d4:34' },
      { timestamp: '20260901_010200', value: 6.2e9, trigger: 'experiment',
        run_id: 99, experiment: '06_ramsey', uid: null },
      // Review round 1, seen in a real browser: the CURATED param_history row
      // carries no run for a snapshot the LEAF index ties to run 64 (the
      // docs/132 reverse-order case). The hint was gated on the uid and
      // numbered from run_id, so it printed "open dataset #null" — and the
      // line above it denied a run had happened while this one offered to
      // open one. Both now read the server's `run`.
      { timestamp: '20260901_010300', value: 6.3e9, trigger: 'save',
        run_id: null, experiment: null, run: 64,
        node: '03_resonator_spectroscopy_single', uid: 'a1b2c3d4:64' },
    ],
  }, null);
  return until(win, function () { return chart.__renders === 1
                                  && (chart.__handlers.plotly_click || []).length; },
               'drawer chart drawn').then(function () {
    const exp = chart.data.filter(function (t) { return t.name === 'Experiment'; })[0];
    ok(exp && exp.customdata.length === 2, '6a the drawer keeps its trigger traces');
    const withUid = exp.customdata.filter(function (cd) { return cd[0] === 34; })[0];
    const noUid = exp.customdata.filter(function (cd) { return cd[0] === 99; })[0];
    ok(withUid[4] === 'a1b2c3d4:34', '6b the server-minted uid rides customdata');
    ok(/click .* open dataset/.test(withUid[3]), '6c the click hint is offered');
    ok(noUid[3] === '',
       '6d ...and withheld from a run whose uid the server could not mint');
    const fireIt = function (cd) {
      chart.__handlers.plotly_click.forEach(function (fn) {
        fn({ points: [{ customdata: cd }] }); }); };
    fireIt(withUid);
    ok(win._htmxCalls.length === 1
       && win._htmxCalls[0][1] === '/dataset/a1b2c3d4:34',
       '6e the click opens /dataset/<folder_key>:<run_id>, not a bare run id');
    win._htmxCalls.length = 0;
    fireIt(noUid);
    ok(win._htmxCalls.length === 0, '6f a run with no uid does nothing');
    // 6g-6j — the tier split: uid from the leaf index, run_id NULL in the
    // curated row. Neither line may print the word "null", and they may not
    // contradict each other.
    const save = chart.data.filter(function (t) { return t.name === 'Save'; })[0];
    const split = save && save.customdata[0];
    ok(split && split[4] === 'a1b2c3d4:64', '6g the tier-split point keeps its uid');
    ok(split && /click .* open dataset #64/.test(split[3]),
       '6h the hint numbers itself from the run the uid opens');
    ok(split && split.every(function (v) { return !/null/.test(String(v)); }),
       '6i no line prints the literal word "null"');
    ok(split && /#64/.test(split[2]),
       '6j ...and the context line names the same run instead of denying one');
    win._htmxCalls.length = 0;
    fireIt(split);
    ok(win._htmxCalls.length === 1
       && win._htmxCalls[0][1] === '/dataset/a1b2c3d4:64',
       '6k the tier-split point opens the run the hint promised');
  });
})());

Promise.all(world).then(function () {
  if (fails === 0) console.log('all checks passed (' + checks + ' assertions)');
  process.exit(fails ? 1 : 0);
}, function (e) {
  console.error('harness error: ' + (e && e.stack || e));
  process.exit(1);
});
