/* QA chipstatus-r2-05 + r2-06 — the threshold editor's save must tell the
 * truth, and must not erase another window's bands.
 *
 * r2-05, measured in real Chrome: with POST /chip-status/spec failing
 * (connection refused, HTTP 500) "Update colour bands" still printed
 * "✓ saved for everyone using this SM", the verdict moved to the unsaved
 * bands, and a reload silently reverted them. _postSpec never checked r.ok
 * and its `.catch` returned null, which the caller read as success.
 *
 * r2-06: tab B, opened before tab A saved T1 40/20, changed only T2 echo and
 * pressed Apply -- and A's T1 was gone. B posted its WHOLE in-memory band set
 * and the server replaced the file with it.
 *
 * Pins (the REAL app.js + topo-graph.js + chip-status.js under jsdom, a fake
 * server that merges like core/spec_thresholds.save):
 *  F  a rejected fetch, an HTML 500 and a 400 {ok:false} each say NOT saved,
 *     keep the typed value marked, and leave the bands on the saved ones;
 *  P  Apply posts only the bound that was edited;
 *  M  a success adopts the server's merged answer (another window's band);
 *  R  reset-one and reset-all failures say NOT reset and change nothing;
 *  O  opening the editor re-reads the server's bands, and a tab coming back
 *     into view never overwrites a value typed and not applied.
 *
 * Run: node tests/thresh_save_selfcheck.cjs
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
  T2echo: { direction: 'higher', warn: 2e-5, fail: 1e-5, label: 'T2 echo' },
};
const topo = {
  nodes: [
    { id: 'qA1', grid_location: '0,0', T1: 2.4e-5, metrics: { T1: { value: 2.4e-5 } } },
    { id: 'qA2', grid_location: '1,0', T1: 1.9e-5, metrics: { T1: { value: 1.9e-5 } } },
  ],
  edges: [], summary: {},
};

// A fake /chip-status/spec that merges per bound, like spec_thresholds.save.
function makeServer() {
  const srv = { stored: {}, mode: 'ok', posts: [], gets: 0 };
  srv.spec = function () {
    const metrics = JSON.parse(JSON.stringify(DEFAULTS));
    const edited = [];
    Object.keys(srv.stored).forEach(function (k) {
      if (!metrics[k]) return;
      Object.keys(srv.stored[k]).forEach(function (b) { metrics[k][b] = srv.stored[k][b]; });
      edited.push(k);
    });
    return { metrics: metrics, edited: edited.sort(),
             source: edited.length ? 'mixed' : 'default',
             summary: edited.length ? 'your lab\'s bands for ' + edited.length : 'SM\'s own default bands' };
  };
  srv.fetch = function (url, opts) {
    const u = String(url).split('?')[0];
    const method = (opts && opts.method) || 'GET';
    if (!/^\/chip-status\/spec/.test(u)) return new Promise(function () {});
    if (method === 'GET') {
      srv.gets++;
      const body = srv.spec();
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
    }
    let posted = null;
    if (u === '/chip-status/spec') {
      posted = JSON.parse(opts.body.get('metrics'));
      srv.posts.push(posted);
    }
    if (srv.mode === 'reject') return Promise.reject(new TypeError('Failed to fetch'));
    if (srv.mode === 'html500') {
      return Promise.resolve({ ok: false, status: 500,
        json: function () { return Promise.reject(new SyntaxError('Unexpected token <')); } });
    }
    if (srv.mode === 'refused') {
      return Promise.resolve({ ok: false, status: 400,
        json: function () { return Promise.resolve({ ok: false, error: 'metrics must be JSON' }); } });
    }
    if (u === '/chip-status/spec/clear') srv.stored = {};
    else {
      Object.keys(posted).forEach(function (k) {
        if (!DEFAULTS[k]) return;
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
    + '<div id="topo-hero"></div><div id="topo-health-tiles"></div>'
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
  return f;
}
function status(win) { const s = win.document.getElementById('thresh-status'); return s ? s.textContent : ''; }
function settle() { return new Promise(function (r) { setTimeout(r, 30); }); }

(async function () {
  /* F: failed saves say so */
  for (const mode of ['reject', 'html500', 'refused']) {
    const srv = makeServer();
    const win = world(srv);
    win.toggleThresholdEditor();
    await settle();
    const saved = field(win, 'T1', 'warn').getAttribute('data-saved');
    const typed = String(parseFloat(saved) + 10);
    srv.mode = mode;
    type(win, 'T1', 'warn', typed);
    win.applyThresholds();
    await settle();
    const st = status(win);
    ok(!/✓ saved/.test(st) && /NOT saved/.test(st),
      'F1 ' + mode + ': the status says the bands were NOT saved — ' + st);
    if (mode === 'refused') ok(/metrics must be JSON/.test(st), 'F2 refused: …and says why the server refused');
    const f = field(win, 'T1', 'warn');
    ok(f && f.value === typed && f.classList.contains('thresh-dirty'),
      'F3 ' + mode + ': the typed value stays in its field, marked not applied — '
      + (f && f.value) + ' dirty=' + (f && f.classList.contains('thresh-dirty')));
    ok(f && f.getAttribute('data-saved') === saved,
      'F4 ' + mode + ': the field\'s saved value is still the server\'s — ' + (f && f.getAttribute('data-saved')));
    ok(Math.abs(win._chipThresholds.T1.warn - DEFAULTS.T1.warn) < 1e-15,
      'F5 ' + mode + ': the bands the verdict uses are the saved ones — ' + win._chipThresholds.T1.warn);
  }

  /* P + M: only the edit is posted, and the merged answer is adopted */
  {
    const srv = makeServer();
    const win = world(srv);
    win.toggleThresholdEditor();
    await settle();
    // another window saves T2 echo after this page rendered
    srv.stored = { T2echo: { warn: 2.5e-5 } };
    const saved = field(win, 'T1', 'fail').getAttribute('data-saved');
    type(win, 'T1', 'fail', String(parseFloat(saved) + 5));
    win.applyThresholds();
    await settle();
    const posted = srv.posts[srv.posts.length - 1] || {};
    ok(JSON.stringify(Object.keys(posted)) === '["T1"]'
       && JSON.stringify(Object.keys(posted.T1 || {})) === '["fail"]',
      'P1: Apply posts only the bound that was edited — ' + JSON.stringify(posted));
    ok(/✓ saved/.test(status(win)), 'P2: …and a stored save says saved — ' + status(win));
    ok(srv.stored.T2echo && srv.stored.T2echo.warn === 2.5e-5,
      'M1: the other window\'s band survived on the server — ' + JSON.stringify(srv.stored));
    ok(win._chipThresholds.T2echo && Math.abs(win._chipThresholds.T2echo.warn - 2.5e-5) < 1e-15,
      'M2: …and this tab adopted it from the merged answer — ' + (win._chipThresholds.T2echo || {}).warn);
    const t2 = field(win, 'T2echo', 'warn');
    ok(t2 && Math.abs(parseFloat(t2.getAttribute('data-saved')) - parseFloat(t2.value)) < 1e-9
       && !t2.classList.contains('thresh-dirty'),
      'M3: …and its editor shows it as saved, not typed');

    /* R: reset-one fails */
    srv.mode = 'reject';
    const before = JSON.stringify(win._chipThresholds);
    win.resetMetricThreshold('T1');
    await settle();
    ok(/NOT reset/.test(status(win)), 'R1: a refused reset-one says NOT reset — ' + status(win));
    ok(JSON.stringify(win._chipThresholds) === before, 'R2: …and the bands did not move');
    /* R: reset-all fails */
    win.resetThresholds();
    await settle();
    ok(/NOT reset/.test(status(win)), 'R3: a refused reset-all says NOT reset — ' + status(win));
    ok(JSON.stringify(win._chipThresholds) === before, 'R4: …and the bands did not move');
    /* reset-one that works posts that metric only */
    srv.mode = 'ok';
    const n = srv.posts.length;
    win.resetMetricThreshold('T1');
    await settle();
    const p2 = srv.posts[srv.posts.length - 1] || {};
    ok(srv.posts.length === n + 1 && JSON.stringify(Object.keys(p2)) === '["T1"]',
      'R5: a reset-one posts that metric only — ' + JSON.stringify(p2));
    ok(!srv.stored.T1 && srv.stored.T2echo, 'R6: …which removes it and nothing else — ' + JSON.stringify(srv.stored));
  }

  /* O: the editor re-reads the server; a typed value is never overwritten */
  {
    const srv = makeServer();
    const win = world(srv);
    srv.stored = { T1: { warn: 4e-5, fail: 2e-5 } };   // tab A saved after B rendered
    win.toggleThresholdEditor();
    await settle();
    ok(srv.gets > 0, 'O1: opening the editor asks the server for its bands');
    ok(Math.abs(win._chipThresholds.T1.warn - 4e-5) < 1e-15,
      'O2: …and adopts another window\'s saved band — ' + win._chipThresholds.T1.warn);
    const f = field(win, 'T1', 'warn');
    ok(f && Math.abs(parseFloat(f.value) - parseFloat(f.getAttribute('data-saved'))) < 1e-9
       && parseFloat(f.value) > 35, 'O3: …and shows it in the editor — ' + (f && f.value));

    srv.stored = { T1: { warn: 5e-5, fail: 2e-5 } };
    type(win, 'T1', 'fail', '15');
    win.document.dispatchEvent(new win.Event('visibilitychange'));
    await settle();
    const g = field(win, 'T1', 'fail');
    ok(g && g.value === '15' && g.classList.contains('thresh-dirty'),
      'O4: a tab coming back never overwrites a typed, unapplied value — ' + (g && g.value));
  }

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('thresh_save_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
