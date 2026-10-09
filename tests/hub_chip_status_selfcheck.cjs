// S10 C7: old -> new, remove snapshot-only payloads and preserve ledger behavior.
/* docs/283 (S8) jsdom selfcheck: what the shipped Chip Status / Param History
 * JS SAYS about a value read from the chip's change ledger.
 *
 *  1. ChipStatus.metaInfo.describe (the metric tile's hover and tag): a run
 *     is "Written by" only when the ledger proves it (provenance run_proven;
 *     the tag then carries its number); an unproven run, a first record and
 *     a waiting ledger say what they are; a value on screen that differs from
 *     the newest recorded one is "not in history"; the lab's own stamp and
 *     the run its node recorded still show.
 *  2. ChipTrends.render: a point's hover carries the server's words (escaped),
 *     and a click opens a run only where the server minted a uid (proof).
 *  3. paramHistoryRenderDrawerChart (app.js): a ledger point's context line is
 *     its own words, a restore is drawn, and a point opens nothing without a uid.
 *
 * Run: node tests/hub_chip_status_selfcheck.cjs  (driven by
 *      tests/test_hub_chip_status.py::test_the_chip_status_selfcheck)
 */
'use strict';
const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.log('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
let n = 0, fails = 0;
function ok(cond, msg) {
  n++;
  if (!cond) { fails++; console.log('FAIL: ' + msg); }
}
function until(test, label, ms) {
  const t0 = Date.now();
  return new Promise(function (resolve, reject) {
    (function poll() {
      let v = false;
      try { v = test(); } catch (e) { v = false; }
      if (v) return resolve(v);
      if (Date.now() - t0 > (ms || 5000)) return reject(new Error('timeout: ' + label));
      setTimeout(poll, 5);
    })();
  });
}

// ── 1) the metric tile's words ───────────────────────────────────────────
const csDom = new JSDOM('<!doctype html><div id="table-pane"></div>', {
  url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
csDom.window.eval(read('chip-status.js'));
const MI = csDom.window.ChipStatus.metaInfo;
const stamp = '2026-01-01T12:00:20Z';
ok(MI.snapMs(stamp) === Date.parse(stamp), '1a a ledger instant (ISO, Z) is read as that instant');

const proven = MI.describe({ ts: stamp, provenance: 'run_proven', run: 2, label: '#2 scan',
  sub: 'its own patch set it', matches_current: true, value: 3e-5 }, { cur: 3e-5 });
ok(proven.lines.some(function (l) { return l === 'Written by: #2 scan — its own patch set it'; }),
   '1b a proven run is named as the writer');
ok(/#2$/.test(proven.tag) && !proven.edited, '1c ...and the tile tag carries its number');

const unproven = MI.describe({ ts: stamp, provenance: 'run_saved', run: null, saved_run: 3,
  label: 'saved in #3 scan', sub: 'writer not proven', matches_current: true, value: 5.1e9 },
  { cur: 5.1e9, notes: ['The ledger is not being kept current in this window.'] });
const ut = unproven.lines.join('\n');
ok(ut.indexOf('Recorded: saved in #3 scan — writer not proven') >= 0,
   '1d an unproven run is recorded, in the ledger\'s own words');
ok(ut.indexOf('Written by') < 0 && !/#3/.test(unproven.tag),
   '1e ...never "Written by", and the tag names no run');
ok(unproven.lines.indexOf('The ledger is not being kept current in this window.') >= 0,
   '1f the ledger\'s notes ride the hover');

const first = MI.describe({ ts: stamp, provenance: 'first_record', first: true,
  label: 'first recorded in #1', sub: 'ledger start; writer unknown', matches_current: true }, {});
ok(/^≤/.test(first.tag) && first.lines.join(' ').indexOf('writer unknown') >= 0,
   '1g a first record is "unchanged since the ledger began", writer unknown');

const edited = MI.describe({ ts: stamp, provenance: 'run_proven', run: 2, label: '#2 scan',
  sub: 'its own patch set it', matches_current: true, value: 3e-5 }, { cur: 4.5e-5 });
ok(edited.edited && edited.tag === 'not in history',
   '1h a value on screen that is not the newest recorded one is "not in history"');
ok(edited.lines[0].indexOf('Not in this chip’s change ledger yet') === 0,
   '1i ...and the hover says so first');

const lab = MI.describe({ ts: stamp, provenance: 'sm', label: 'applied by user-a',
  sub: 'SM write (apply-to-live)', matches_current: true, load_id: 77, undone: 'undone',
  flags: ['run folder deleted'] }, { stamp: Date.parse(stamp) });
const lt = lab.lines.join('\n');
ok(lt.indexOf('Written by: applied by user-a') >= 0, '1j an SM write names its actor and kind');
ok(lt.indexOf('Run recorded by the lab’s node: #77') >= 0, '1k the lab node\'s run still shows');
ok(lt.indexOf('Measured (the lab’s own stamp)') >= 0, '1l the lab\'s own stamp still shows');
ok(lt.indexOf('A later undo took this write back.') >= 0 && lt.indexOf('run folder deleted') >= 0,
   '1m undone and flags are said');
// S10 C5 (C3 review): a value that appeared at its newest change says so
const appeared = MI.describe({ ts: stamp, provenance: 'run_proven', run: 2, label: '#2 scan',
  sub: 'its own patch set it', matches_current: true, value: 3e-5, appeared: true }, { cur: 3e-5 });
ok(/^First recorded: /.test(appeared.lines[0]) && /^Last changed: /.test(proven.lines[0]),
   '1n a value that appeared at its newest change is "First recorded", any other "Last changed"');

const waiting = MI.describe(null, { mode: 'building',
  message: 'The change history is being built (2 of 9 runs). It shows here when it is complete.' });
ok(waiting.tag === 'history pending' && waiting.lines[0].indexOf('2 of 9') >= 0,
   '1n a building ledger is said, with nothing dated');

// ── 2) a Trends point's hover and click ──────────────────────────────────
async function trends() {
  const w = csDom.window;
  w.document.body.insertAdjacentHTML('beforeend', '<div id="topo-trends"><div class="topo-trends-grid">'
    + '<div class="topo-trend-box" data-trend-metric="T1"><div id="topo-trend-0" class="topo-trend-chart"></div></div>'
    + '</div></div>');
  w.SnapTime = { axisValue: function () { return '2026-01-01T21:00:00'; },
                 label: function () { return 'test zone'; } };
  w.requirePlotly = function () { return w.Promise.resolve({}); };
  w._plotlyRender = function (el, data, layout) {
    el.data = data; el.layout = layout; el.handlers = {};
    el.on = function (name, fn) { el.handlers[name] = fn; };
    el.removeAllListeners = function (name) { delete el.handlers[name]; };
    return w.Promise.resolve(el);
  };
  const opened = [];
  w.htmx = { ajax: function (method, url, opts) { opened.push({ method: method, url: url, opts: opts }); } };
  const k1 = '20260101_120010_e1', k2 = '20260101_120020_e2';
  w.ChipTrends.render([{ metric: 'T1', series: [{ entity: 'qA1', points: [[k1, 1], [k2, 2]],
    attr: { [k1]: { provenance: 'run_saved', label: 'saved in #3', sub: 'writer not proven', uid: null },
            [k2]: { provenance: 'run_proven', label: '#4 <scan>', sub: 'its own patch set it',
                    flag_text: ['run folder rewritten after the run'], uid: 'root:4' } } }] }]);
  const host = w.document.getElementById('topo-trend-0');
  await until(function () { return host.handlers && host.handlers.plotly_click; }, 'trends click bound');
  const trace = host.data[0];
  ok(trace.customdata[0][1].indexOf('saved in #3<br>writer not proven') >= 0,
     '2a an unproven point says so in its hover');
  ok(trace.customdata[0][3] === null && trace.customdata[0][2] === '',
     '2b ...offers no click and no click hint');
  ok(trace.customdata[1][1].indexOf('#4 &lt;scan&gt;') >= 0, '2c the server\'s words are escaped');
  ok(trace.customdata[1][1].indexOf('run folder rewritten after the run') >= 0, '2d flags are said');
  host.handlers.plotly_click({ points: [{ customdata: trace.customdata[0] }] });
  ok(opened.length === 0, '2e a click on an unproven point opens nothing');
  host.handlers.plotly_click({ points: [{ customdata: trace.customdata[1] }] });
  ok(opened.length === 1 && opened[0].url === '/dataset/root:4'
     && opened[0].opts.target === '#inspector-pane', '2f a proven point opens its run');
}

// ── 3) the Param History drawer on the ledger ────────────────────────────
async function drawer() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="param-history-drawer">'
    + '<div id="phd-chart"></div></div><div id="table-pane"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  const calls = [];
  win.htmx = { ajax: function () { calls.push(Array.prototype.slice.call(arguments)); } };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.Plotly = { newPlot: function (id, data, layout) {
    const el = win.document.getElementById(id);
    el.data = data; el.layout = layout; el.__renders = (el.__renders || 0) + 1; el.__handlers = {};
    el.on = function (name, fn) { (el.__handlers[name] = el.__handlers[name] || []).push(fn); };
    return win.Promise.resolve(el);
  } };
  win.eval(read('app.js'));
  win.requirePlotly = function () { return win.Promise.resolve(win.Plotly); };
  const chart = win.document.getElementById('phd-chart');
  win.paramHistoryRenderDrawerChart({ property: 'T1', ledger: true, values: [
    { timestamp: '20260101_120010_e1', value: 1e-5, trigger: 'experiment', label: 'first recorded in #1',
      sub: 'ledger start; writer unknown', flags: [], uid: null, run: null },
    { timestamp: '20260101_120020_e2', value: 3e-5, trigger: 'experiment', label: '#2 <scan>',
      sub: 'its own patch set it', flags: [], uid: 'root:2', run: 2, node: 'scan' },
    { timestamp: '20260101_120030_e3', value: 4e-5, trigger: 'restore', label: 'restored by user-a',
      sub: 'SM write (restore-live)', flags: [], uid: null, run: null },
  ] }, null);
  await until(function () { return chart.__renders === 1 && (chart.__handlers.plotly_click || []).length; },
              'drawer chart drawn');
  const exp = chart.data.filter(function (t) { return t.name === 'Experiment'; })[0];
  const first = exp.customdata[0], proven = exp.customdata[1];
  ok(first[2] === 'first recorded in #1 · ledger start; writer unknown' && first[3] === '' && first[4] === '',
     '3a a first record says so, with no click');
  ok(proven[2].indexOf('#2 &lt;scan&gt; · its own patch set it') === 0 && /open dataset #2/.test(proven[3]),
     '3b a proven point says so (escaped) and offers its run');
  ok(chart.data.some(function (t) { return t.name === 'Restore' && t.x.length === 1; }),
     '3c a restore on the ledger is drawn');
  chart.__handlers.plotly_click.forEach(function (fn) { fn({ points: [{ customdata: first }] }); });
  ok(calls.length === 0, '3d a click on an unproven point opens nothing');
  chart.__handlers.plotly_click.forEach(function (fn) { fn({ points: [{ customdata: proven }] }); });
  ok(calls.length === 1 && calls[0][1] === '/dataset/root:2', '3e a proven point opens its run');
  dom.window.close();
}

trends().then(drawer).then(function () {
  csDom.window.close();
  if (fails) { console.log(fails + ' of ' + n + ' checks FAILED'); process.exit(1); }
  console.log('all ' + n + ' checks passed');
}, function (err) {
  console.log('FAIL: ' + (err && err.stack || err));
  process.exit(1);
});
