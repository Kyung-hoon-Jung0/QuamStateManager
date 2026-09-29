/* docs/226 -- the hand-edited URL sites outside app.js, under a '/sm' mount.
 *
 * The request sinks (fetch, htmx, pushState) are prefixed by sm-root.js on
 * their own (sm_root_selfcheck, prefix_hooks_selfcheck). What they cannot
 * reach is a place that COMPARES a location/request path with an app route,
 * or hands a URL to something that is not a wrapped sink (a plain link, a
 * navigation, window.open). Each such site was edited to go through
 * window.SM.path / window.SM.url; each is driven here through the REAL
 * shipped file under jsdom, with sm-root.js booted at '/sm' (and, where the
 * URL is visible, at root as the control):
 *
 *  CS*  chip-status.js -- a tab/tile jump writes its place record with the
 *       URL the location actually has (F5's _chipScrollRecord compares the
 *       two), the scroll record is written on /sm/topology, isRefresh knows a
 *       prefixed /topology request, the report and Diagnostics tile links
 *  CH*  compare-hub.js -- the basket reads location.search on /sm/compare-hub
 *       (a second add keeps the first source) and Back re-fetches the page
 *  DP1  diff-panes.js -- a baseline click keeps ?base= in a /sm/diff URL
 *  ZL1  zline.js -- picking a line keeps ?line= in a /sm/zline URL
 *  CW*  calc.js -- the calculator window's fallback URL
 *  PE1  pair-edit.js -- opening a pair asks for /sm/pair/<id>
 *  AG*  agent.js / agent-setup.js -- the links they build (run, plan revert,
 *       Calibration log, Setup, the wire strip's fix, Experiment Runner)
 *
 * Run: node tests/prefix_sites_selfcheck.cjs   (driven by tests/test_js_root_lint.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const SMROOT = read('sm-root.js');
const ORIGIN = 'http://127.0.0.1:5050';

let fails = 0, passes = 0;
function ok(c, m) { if (c) { passes++; console.log('ok - ' + m); } else { fails++; console.error('FAIL: ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

/* jsdom window at ORIGIN + at, data-root = root, sm-root.js booted the way
   the harnesses boot it (Node realm, window only) AFTER the given stubs. */
function win(root, at, body, stubs) {
  const dom = new JSDOM('<!doctype html><html data-root="' + root + '"><body>' + (body || '') + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: ORIGIN + at });
  const w = dom.window;
  w.console.error = function () {};
  if (stubs) stubs(w);
  new Function('window', SMROOT)(w);
  return w;
}
const loc = (w) => w.location.pathname + w.location.search;

(async function main() {
  // ── CS: chip-status.js ───────────────────────────────────────────────
  {
    const VIEWS = ['overview', 'health', 'topology', 'trends', 'fidelity2q', 'fidelity1q', 'readout', 'coherence', 'frequencies', 'calibration'];
    const PAGE = '<ul id="chip-status-subnav">' + VIEWS.map((v) => '<li><a data-view="' + v + '">' + v + '</a></li>').join('') + '</ul>'
      + '<div id="table-pane"><div class="topo-dashboard">'
      + '<div class="topo-section" data-topo-section="overview"><div id="topo-overview-tiles"></div></div>'
      + '<div class="topo-section" data-topo-section="health"><div id="topo-health-tiles"></div></div>'
      + '<div class="topo-subnav">' + VIEWS.map((v) => '<button class="topo-subnav-btn" data-view="' + v + '"></button>').join('') + '</div>'
      + '<div class="topo-section" id="sec-topology"><div id="topo-hero"></div><div id="topo-html-wrap"></div></div>'
      + '<div class="topo-section" data-topo-section="trends"><div id="topo-trends"></div></div>'
      + '<div class="topo-section" data-topo-section="fidelity" id="sec-fidelity"><div id="topo-2q-rb-panels" data-topo-section="2qrb"></div></div>'
      + '<div class="topo-section" data-topo-section="fid1q" id="sec-fidelity-1q"><div id="topo-fidelity-1q-panels"></div></div>'
      + '<div class="topo-section" data-topo-section="fidro" id="sec-readout"><div id="topo-fidelity-ro-panels"></div></div>'
      + '<div id="topo-metric-panels" data-topo-section="metrics"></div>'
      + '</div></div>';
    const node = (id, l) => ({ id: id, grid_location: l, T1: 12e-6, T2ramsey: 15e-6, f_01: 5.1e9, readout_frequency: 7.2e9,
                               x180_amplitude: 0.12, x90_amplitude: 0.06, gate_fidelity_avg: 0.998, assignment_fidelity: 0.95 });
    const CHAIN = { nodes: ['0,0', '1,0', '2,0'].map((l, i) => node('q' + (i + 1), l)),
                    edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, gate_kind: 'cz', gate_fidelities: [] }] };
    const CODE = read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n' + read('chip-status.js');
    const csWorld = function (root) {
      const w = win(root, root + '/topology?view=overview', PAGE, function (w) {
        w.htmx = { ajax: function () { return new w.Promise(function () {}); }, process: function () {} };
        w.fetch = function () { return new w.Promise(function () {}); };
        w.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
        w.ResizeObserver = function () { this.observe = function () {}; this.unobserve = function () {}; this.disconnect = function () {}; };
        w.Plotly = {};
        w.Element.prototype.scrollIntoView = function () {};   // jsdom has none
      });
      new w.Function(CODE).call(w);
      w._plotlyRender = function () { return new w.Promise(function () {}); };
      w.history.replaceState({ htmx: true }, '');
      w.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(CHAIN)), rawWiring: {}, defaultThresholds: {},
                           diagFindings: [], metricMeta: {}, chipView: '' });
      return w;
    };
    for (const root of ['/sm', '']) {
      const tag = root ? '/sm' : 'root';
      const w = csWorld(root);
      const d = w.document;
      const btn = d.querySelector('.topo-subnav-btn[data-view="coherence"]');
      w.setChipStatusView('coherence', btn, true);
      const rec = w.history.state && w.history.state.smChipScroll;
      ok(loc(w) === root + '/topology?view=coherence',
         'CS1 ' + tag + ': a tab-bar jump writes the URL ' + loc(w));
      ok(rec && rec.url === loc(w) && rec.jump === true,
         'CS2 ' + tag + ': ...and its place record names the SAME url (what F5 compares): ' + (rec && rec.url));
      const a = d.createElement('a');
      w.ChipStatus.reportHref(a, 'pdf');
      ok(a.getAttribute('href') === root + '/topology/report?format=pdf', 'CS3 ' + tag + ': the report link is ' + a.getAttribute('href'));
      const tiles = Array.prototype.filter.call(d.querySelectorAll('#topo-health-tiles a.topo-health-tile'),
        (t) => /\/diagnostics$/.test(t.getAttribute('hx-get') || ''));
      ok(tiles.length === 1 && tiles[0].getAttribute('href') === root + '/diagnostics' && tiles[0].getAttribute('hx-get') === root + '/diagnostics',
         'CS4 ' + tag + ': the Diagnostics health tile links ' + (tiles[0] && tiles[0].getAttribute('href')));
      const R = w.ChipStatus.paneResume.isRefresh;
      const rq = (p) => ({ target: { id: 'table-pane' }, shouldSwap: true, requestConfig: { elt: d.body, path: p, verb: 'get' } });
      ok(R(rq(root + '/topology?view=trends')) === true && R(rq(root + '/topology')) === true
         && R(rq(root + '/topologyx')) === false && R(rq(root + '/qubits')) === false,
         'CS5 ' + tag + ': isRefresh knows a ' + (root || '(root)') + '/topology request and nothing else');
    }
    {
      const w = csWorld('/sm');
      w.document.getElementById('table-pane').dispatchEvent(new w.Event('scroll'));
      await tick(350);
      const rec = w.history.state && w.history.state.smChipScroll;
      ok(rec && rec.url === '/sm/topology?view=overview',
         'CS6 /sm: scrolling Chip Status records the reader\'s place on /sm/topology: ' + (rec && JSON.stringify(rec)));
    }
  }

  // ── CH: compare-hub.js ───────────────────────────────────────────────
  {
    const ajax = [];
    const w = win('/sm', '/sm/compare-hub?src=ws%3A%2Fa', '<div id="table-pane"><div id="cmp-hub-root" data-bucket="1" data-preset="lab" data-ref="0" data-sources="1"></div></div>',
      function (w) {
        w.htmx = { ajax: function (m, u) { ajax.push(u); return new w.Promise(function () {}); } };
        w.fetch = function () { return new w.Promise(function () {}); };
      });
    w.eval(read('compare-hub.js'));
    await tick(20);                                     // its DOMContentLoaded init
    w.cmpHub.add('ws:/b');
    const srcs = new w.URLSearchParams(w.location.search).getAll('src');
    ok(w.location.pathname === '/sm/compare-hub' && srcs.join(',') === 'ws:/a,ws:/b',
       'CH1 on /sm/compare-hub a second add keeps the first source: ' + loc(w));
    const n = ajax.length;
    w.dispatchEvent(new w.PopStateEvent('popstate', { state: { cmpHub: true } }));
    ok(ajax.length === n + 1 && ajax[n] === loc(w), 'CH2 Back to a basket state re-fetches this page: ' + ajax[n]);
  }

  // ── DP1: diff-panes.js ───────────────────────────────────────────────
  {
    const cell = (i, v) => '<td class="dp-cell' + (i === 0 ? ' dp-base' : '') + '" data-i="' + i + '" data-v="' + v + '"><code>' + v + '</code><span class="dp-delta" hidden></span></td>';
    const HEAD = [0, 1].map((i) => '<th class="dp-pane-head' + (i === 0 ? ' dp-base' : '') + '" data-i="' + i + '"><button type="button" class="dp-pane-title" data-i="' + i + '"><span class="dp-slot">' + i + '</span></button></th>').join('');
    const DOM = '<div id="diff-root" data-base="0" data-n="2"><form class="diff-wb-pickers"><input type="hidden" name="base" value="0"></form>'
      + '<div id="diff-panes" data-base="0" data-n="2" data-more="0"><table id="diff-panes-table"><thead><tr><th class="dp-path-col">Leaf</th>' + HEAD + '</tr></thead><tbody>'
      + '<tr class="dp-row dp-leaf" data-eq="10" data-path="qubits.q1.T1" data-parent="qubits.q1" data-depth="2"><td class="dp-key-col"><code class="dot-path">qubits.q1.T1</code></td>' + cell(0, '1') + cell(1, '2') + '</tr>'
      + '</tbody></table></div></div>';
    const w = win('/sm', '/sm/diff?a=x&b=y&tab=state&base=0', DOM, function (w) {
      w.fetch = function () { return new w.Promise(function () {}); };
      w.htmx = { ajax: function () { return w.Promise.resolve(); }, trigger: function () {}, process: function () {} };
      w.requestAnimationFrame = function (f) { return setTimeout(f, 0); };
      w.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
      w.ResizeObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
    });
    w.eval(read('search-query.js'));
    w.eval(read('app.js'));                             // window.ValueDelta
    w.eval(read('diff-panes.js'));
    await tick(20);
    w.document.querySelectorAll('.dp-pane-title')[1].click();
    ok(w.location.pathname === '/sm/diff' && /[?&]base=1(&|$)/.test(w.location.search),
       'DP1 on /sm/diff a baseline click keeps ?base= in the URL: ' + loc(w));
  }

  // ── ZL1: zline.js ────────────────────────────────────────────────────
  {
    const fetched = [];
    const w = win('/sm', '/sm/zline', '<div id="zline-root"><table><tr class="zline-row" data-line="q1.z"><td>q1</td></tr></table>'
      + '<div id="zline-title"></div><div id="zline-step"></div><div id="zline-pulse"></div></div>', function (w) {
      w.fetch = function (u) { fetched.push(u); return new w.Promise(function () {}); };
    });
    w.eval(read('zline.js'));
    w.ZLine.mount();
    w.document.querySelector('.zline-row td').click();
    ok(fetched[0] && fetched[0].indexOf('/sm/zline/data?line=q1.z') === 0, 'ZL1 picking a line fetches ' + fetched[0]);
    ok(w.location.pathname === '/sm/zline' && /[?&]line=q1\.z(&|$)/.test(w.location.search),
       'ZL1 ...and keeps ?line= in the /sm/zline URL: ' + loc(w));
  }

  // ── CW: calc.js ──────────────────────────────────────────────────────
  for (const root of ['/sm', '']) {
    const opened = [];
    const w = win(root, root + '/qubits', '', function (w) {
      w.BroadcastChannel = undefined;                    // no channel: open at once
      w.open = function (u) { opened.push(u); return { closed: false, focus: function () {} }; };
    });
    w.eval(read('calc.js'));
    w.openCalcWindow(null);
    ok(opened.length === 1 && opened[0].split('?')[0] === root + '/calc-window',
       'CW1 ' + (root || 'root') + ': with no data-calc-window-url on the page the window opens ' + opened[0]);
  }

  // ── PE1: pair-edit.js ────────────────────────────────────────────────
  for (const root of ['/sm', '']) {
    const ajax = [];
    const w = win(root, root + '/bulk', '<div id="inspector-pane"></div>', function (w) {
      w.htmx = { ajax: function (m, u) { ajax.push(u); return w.Promise.resolve(); }, process: function () {} };
      w.fetch = function () { return new w.Promise(function () {}); };
    });
    w.eval(read('search-query.js'));
    w.eval(read('pair-edit.js'));
    w.BulkPairEdit.openPair('q1-q2');
    ok(ajax[0] === root + '/pair/q1-q2', 'PE1 ' + (root || 'root') + ': opening a pair asks for ' + ajax[0]);
  }

  // ── AG: agent.js + agent-setup.js ────────────────────────────────────
  for (const root of ['/sm', '']) {
    const tag = root || 'root';
    const now = Date.now() / 1000;
    const feed = {
      ok: true, chip: 'PJ', last: 1, agent_seq: 1, qubits: 2,
      cards: [{ n: 1, ts: now - 60, kind: 'user', text: 'hi', who: 'human:k' }],
      live: {
        plans: [{ id: 'pl-1', title: 'bringup', status: 'done', source: 'agent', created_by: 'by_claude', created: now - 30, mode: 'ask-writes',
                  pre_ts: now - 40, steps: [{ i: 0, node: '05_power_rabi', targets: ['q1'], status: 'done' }], counts: { total: 1, done: 1 }, may_change: [] }],
        runs: [{ key: 'r1', node: '02_res', targets: ['q1'], status: 'ended', since: now - 200,
                 result: { status: 'done', classification: 'ok', run_id: 77, applied: true, writes: [] } }],
        approvals: [],
      },
      session: { alive: false, busy: false, backend: 'claude', ended: null },
      file: { owner: 'human:k', backend: 'claude', armed: false, stopped: false },
      now: { state: 'between', session: { backend: 'claude' }, events_today: 1, failures_today: 0, waiting: 0, mode: 'ask-writes' },
    };
    const WIRE = '<div class="ag-wire" data-ag-wire="1"><span class="ag-wire-badge ag-wire-static">CHECKING</span>'
      + '<span class="ag-wire-cli muted"></span><span class="ag-wire-tail"><a class="ag-wire-setup" href="' + root + '/agent/setup">Setup</a></span></div>';
    const w = win(root, root + '/agent', WIRE + '<div id="agent-home"></div><div id="agent-setup"><div id="as-body"></div></div>', function (w) {
      w.confirm = function () { return true; };
      w.fetch = function (u) {
        let body = { ok: true };
        if (/\/chat\/cards/.test(u)) body = feed;
        else if (/\/chat\/backends/.test(u)) body = { ok: true, chip: 'PJ', default: 'claude', backends: { claude: { found: true, version: '9' }, codex: { found: false } } };
        else if (/\/api\/agent\/setup$/.test(u)) body = { ok: true, clis: { claude: { found: true, version: '9' }, codex: { found: false } },
          claude: { mcp: false, hooks: false, allow: false }, codex: { mcp: false }, calibrations_folder: 'D:/lab/cal', record: { tested: {} },
          todo: [], journal: { root: 'D:/j', configured: true }, context: {}, global_simulate: true };
        else if (/\/scheduler\/settings$/.test(u)) body = { global_simulate: true };
        return w.Promise.resolve({ status: 200, json: function () { return w.Promise.resolve(body); } });
      };
    });
    w.eval(read('agent-pill.js'));
    w.eval(read('agent.js'));
    w.eval(read('agent-setup.js'));
    w.AgentPanel.init();
    await tick(40);
    const d = w.document;
    const attrs = (sel) => { const a = d.querySelector(sel); return a ? [a.getAttribute('href'), a.getAttribute('hx-get')] : [null, null]; };
    const both = (sel, want) => { const x = attrs(sel); return x[0] === want && x[1] === want; };
    ok(both('a.ag-run', root + '/dataset/by-run/77'), 'AG1 ' + tag + ': the run link is ' + attrs('a.ag-run').join(' | '));
    ok(both('a.ag-revert', root + '/state-history'), 'AG2 ' + tag + ': the plan\'s "state before this plan" link is ' + attrs('a.ag-revert').join(' | '));
    ok(both('.ag-now-links a:not(.ag-setup-link)', root + '/journal') && both('.ag-now-links a.ag-setup-link', root + '/agent/setup'),
       'AG3 ' + tag + ': the Calibration log / Setup links are ' + attrs('.ag-now-links a:not(.ag-setup-link)')[0] + ' , ' + attrs('.ag-now-links a.ag-setup-link')[0]);
    ok(both('.ag-wire-fix', root + '/agent/setup'), 'AG4 ' + tag + ': the wire strip\'s Connect fix is ' + attrs('.ag-wire-fix').join(' | '));
    w.AgentSetup.init();
    await tick(40);
    const sched = Array.prototype.filter.call(d.querySelectorAll('#as-body a[hx-get]'), (a) => /\/scheduler$/.test(a.getAttribute('hx-get')));
    ok(sched.length === 1 && sched[0].getAttribute('href') === root + '/scheduler' && sched[0].getAttribute('hx-get') === root + '/scheduler',
       'AG5 ' + tag + ': Agent Setup\'s Experiment Runner link is ' + (sched[0] && sched[0].getAttribute('href')));
  }

  console.log(passes + ' passed, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
