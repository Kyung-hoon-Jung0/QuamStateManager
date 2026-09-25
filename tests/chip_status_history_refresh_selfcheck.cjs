/* QA chipstatus-r2-15 — Chip Status follows the chip's history after a capture.
 *
 * Measured on the customer chip copy: open /topology?view=trends and History
 * (1594), press Take Snapshot. The hover popup said "1595 snapshots"; the
 * History button and the Trends label both stayed at 1594. The drawer's POST
 * swaps only #history-content, the Trends section is a one-shot lazy fetch,
 * and nothing on Chip Status listened for stateHistoryChanged. The page's
 * sparkline gate (_historyCount) was frozen at render too, so on a chip with
 * no snapshots the popup trends never loaded until a reload.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom):
 *  H1  stateHistoryChanged re-fetches a built Trends section, once for a burst;
 *  H2  it keeps the section's current selection (the same _params() request);
 *  H3  the sparkline gate opens once a capture is announced;
 *  H4  navigating away tears the listener down;
 *  H5  a Trends section that was never built (never scrolled near) is not
 *      fetched by a capture -- building it stays the lazy observer's call.
 *
 * Run: node tests/chip_status_history_refresh_selfcheck.cjs
 *      (driven by tests/test_history_drawer.py)
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
  nodes: [{ id: 'qA1', grid_location: '0,0', T1: 2.4e-5, metrics: { T1: { value: 2.4e-5 } } },
          { id: 'qA2', grid_location: '1,0', T1: 1.9e-5, metrics: { T1: { value: 1.9e-5 } } }],
  edges: [], summary: {},
};

function world(withTrends, lazy) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>'
    + '<button class="history-toggle-btn">History (<span id="history-count">0</span>)</button>'
    + '<div id="table-pane"><div class="topo-dashboard">'
    + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
    + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
    + (withTrends ? '<div class="topo-section" data-topo-section="trends"><div id="topo-trends">'
        + '<button class="topo-trend-chip active" data-trend-metric="T1"></button></div></div>' : '')
    + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/topology?view=trends' });
  const win = dom.window;
  win.__ajax = [];
  win.__fetches = [];
  win.htmx = { process: function () {},
               ajax: function (verb, url, spec) { win.__ajax.push({ verb: verb, url: url, spec: spec }); } };
  win.fetch = function (url) { win.__fetches.push(String(url)); return new Promise(function () {}); };
  // lazy: a real observer that never reports an intersection, so the Trends
  // section stays unbuilt (without one, jsdom builds every section at mount)
  if (lazy) win.IntersectionObserver = function () { return { observe: function () {}, disconnect: function () {} }; };
  new win.Function(SRC).call(win);
  win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(topo)), rawWiring: {}, diagFindings: [],
                         metricMeta: {}, defaultThresholds: {}, historyCount: 0 });
  return win;
}
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function trendsGets(win) {
  return win.__ajax.filter(function (c) { return /^\/topology\/trends\?/.test(c.url); }).length;
}
function announce(win) {
  win.document.body.dispatchEvent(new win.CustomEvent('stateHistoryChanged', { bubbles: true }));
}

(async function () {
  {
    const win = world(true);
    await wait(20);
    ok(win.__ajax.some(function (c) { return c.url === '/topology/trends'; }),
      'H0: (the Trends section was built by its first fetch)');
    const n0 = trendsGets(win);
    win.document.getElementById('history-count').textContent = '3';   // what the OOB span does
    announce(win); announce(win);                                     // header + drift poll
    await wait(500);
    ok(trendsGets(win) === n0 + 1, 'H1: a capture re-fetches Trends, once for a burst — '
       + (trendsGets(win) - n0) + ' request(s)');
    const last = win.__ajax[win.__ajax.length - 1] || {};
    ok(/metrics=T1/.test(last.url) && last.spec && last.spec.target === '#topo-trends',
      'H2: …with the section\'s own selection, into the section — ' + last.url);

    /* H3: the popup's sparkline fetch was gated on the render-time count (0) */
    const g = win.document.querySelector('[data-hero-qubit="qA1"]');
    g.dispatchEvent(new win.MouseEvent('mouseenter', { bubbles: true }));
    await wait(350);
    ok(win.__fetches.some(function (u) { return /\/api\/topology\/sparklines\/qA1/.test(u); }),
      'H3: after a capture the popup fetches its trends — ' + JSON.stringify(win.__fetches.filter(function (u) { return /spark/.test(u); })));

    /* H4: navigate away, then announce */
    const nav = new win.CustomEvent('htmx:beforeSwap', { bubbles: true,
      detail: { target: win.document.getElementById('table-pane') } });
    win.document.body.dispatchEvent(nav);
    const n1 = trendsGets(win);
    announce(win);
    await wait(500);
    ok(trendsGets(win) === n1, 'H4: after navigating away nothing is re-fetched — ' + (trendsGets(win) - n1));
  }
  {
    const win = world(true, true);
    await wait(20);
    ok(!win.__ajax.some(function (c) { return /trends/.test(c.url); }), 'H5a: (the section is not built yet)');
    announce(win);
    await wait(500);
    ok(!win.__ajax.some(function (c) { return /trends/.test(c.url); }),
      'H5b: a capture does not build an unbuilt Trends section — '
      + JSON.stringify(win.__ajax.map(function (c) { return c.url; })));
  }

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_history_refresh_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
