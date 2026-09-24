// QA round 2 (package cs-ui, chunk 3) -- against the REAL, mounted
// chip-status.js under jsdom:
//
//   F-22              the Health banner's "avoid" list names the metric that
//                     fails (it read as caused by the structural count printed
//                     right before it), each chip titled with its values, and
//                     "Needs attention" lists the lowest readout fidelity
//   chipstatus-r2-04  Back/Forward (htmx history restore: the body is
//                     replaced WITHOUT a beforeSwap) no longer leaves the
//                     previous mount's document handlers, live poller and
//                     state listeners running -- counts stay flat, the poller
//                     stops on the page Back lands on, one state change sends
//                     one /api/topology
//   chipstatus-r2-14  the "Report" menu (a native <details>) closes on
//                     Escape and on an outside click -- including a click the
//                     Overview's tile kebab stops from propagating -- and after
//                     a format is picked; the listener leaves with the page
//
// Run: node tests/chip_status_qa3_selfcheck.cjs   (needs jsdom)
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
const APP_JS = read('app.js'), TOPO_JS = read('topo-graph.js'), CS_JS = read('chip-status.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// the parts of _wiring.html these checks read, in page order
const DASH = '<div class="topo-dashboard">'
  + '<div class="topo-section" data-topo-section="overview"><button id="ov-settings-btn"></button>'
  + '<span id="ov-custom-note" hidden></span><div class="topo-summary-cards" id="topo-overview-tiles"></div></div>'
  + '<div class="topo-section topo-health-section" data-topo-section="health">'
  + '<div class="topo-verdict-banner" id="topo-verdict-banner" hidden></div>'
  + '<div class="topo-health-head">'
  + '<details class="topo-report-export"><summary class="btn-sm outline">Report</summary>'
  + '<div class="topo-report-menu"><a class="btn-sm" id="rep-md" href="/topology/report?format=md" download'
  + ' onclick="return ChipStatus.reportHref(this,\'md\')">Markdown</a></div></details></div>'
  + '<div class="topo-health-tiles" id="topo-health-tiles"></div>'
  + '<div class="topo-health-worst" id="topo-health-worst"></div></div>'
  + '<div class="topo-section" id="sec-topology"><div id="topo-hero"></div><div id="topo-html-wrap"></div></div>'
  + '<div class="topo-section" data-topo-section="trends"><div id="topo-trends"></div></div>'
  + '<div id="topo-metric-panels" data-topo-section="metrics"></div>'
  + '</div>';
const PAGE = '<!DOCTYPE html><html><body><div id="table-pane">' + DASH + '</div></body></html>';
const QUBITS_PAGE = '<div id="table-pane"><div class="component-page">qubits</div></div>';

// F-22's chip: q2, q4, q5 fail readout (GE) below 90 %; q2 also fails T1.
function node(id, loc, ro, t1) {
  return { id: id, grid_location: loc, T1: t1, gate_fidelity_avg: 0.998, assignment_fidelity: ro,
           metrics: { T1: { value: t1 }, gate_fidelity_avg: { value: 0.998 },
                      assignment_fidelity: { value: ro } } };
}
const TOPO = {
  nodes: [node('q1', '0,0', 0.955, 30e-6), node('q2', '1,0', 0.8848, 5e-6), node('q3', '2,0', 0.951, 30e-6),
          node('q4', '3,0', 0.8790, 30e-6), node('q5', '4,0', 0.8847, 30e-6)],
  edges: [],
  summary: {},
};
const THRESH = {
  gate_fidelity_avg: { direction: 'higher', warn: 0.99, fail: 0.95 },
  assignment_fidelity: { direction: 'higher', warn: 0.95, fail: 0.90 },
  T1: { direction: 'higher', warn: 2e-5, fail: 1e-5 },
};
const META = {
  gate_fidelity_avg: { label: '1Q gate fidelity', abbr: 'Gate F', direction: 'higher' },
  assignment_fidelity: { label: 'Readout fidelity (GE)', abbr: 'Read. Fid. (GE)', direction: 'higher' },
  T1: { label: 'T1', abbr: 'T1', direction: 'higher' },
};
const DIAG = [{ severity: 'warning', title: 'a' }, { severity: 'warning', title: 'b' }];

function world() {
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', pretendToBeVisual: true,
                               url: 'http://localhost/topology?view=health' });
  const win = dom.window;
  const T = { win: win, doc: win.document, fetches: [], live: new Set(), listeners: [] };
  // every (target, type, fn, capture) currently registered on document / body
  const EP = win.EventTarget.prototype, add = EP.addEventListener, rem = EP.removeEventListener;
  const cap = (o) => !!(o === true || (o && o.capture));
  EP.addEventListener = function (type, fn, o) {
    if (this === win.document || this === win.document.body) {
      if (!T.listeners.some((l) => l.t === this && l.type === type && l.fn === fn && l.c === cap(o))) {
        T.listeners.push({ t: this, type: type, fn: fn, c: cap(o) });
      }
    }
    return add.call(this, type, fn, o);
  };
  EP.removeEventListener = function (type, fn, o) {
    T.listeners = T.listeners.filter((l) => !(l.t === this && l.type === type && l.fn === fn && l.c === cap(o)));
    return rem.call(this, type, fn, o);
  };
  T.count = function (where, type) {
    const t = where === 'doc' ? win.document : win.document.body;
    return T.listeners.filter((l) => l.t === t && l.type === type).length;
  };
  const si = win.setInterval, ci = win.clearInterval;
  win.setInterval = function (fn, ms) { const id = si.call(win, fn, ms); T.live.add(id); return id; };
  win.clearInterval = function (id) { T.live.delete(id); return ci.call(win, id); };
  win.htmx = { ajax: function () {}, process: function () {} };
  win.fetch = function (url) {
    T.fetches.push(String(url));
    return win.Promise.resolve({ ok: true, status: 200,
      json: function () { return win.Promise.resolve({ changed: false }); } });
  };
  win.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
  win.Element.prototype.scrollIntoView = function () {};
  win.Plotly = {};
  win._plotlyRender = function () { return new win.Promise(function () {}); };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  // what _wiring.html's inline script does on every render
  T.mount = function () {
    win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(TOPO)), rawWiring: {}, defaultThresholds: THRESH,
                           diagFindings: DIAG, metricMeta: META, chipView: 'health' });
    win.ChipStatus.liveDetection();
  };
  T.capClicks = () => T.listeners.filter((l) => l.t === win.document && l.type === 'click' && l.c).length;
  // app.js's own document listeners, before any Chip Status mount
  T.pre = { click: T.count('doc', 'click'), key: T.count('doc', 'keydown'), cap: T.capClicks() };
  T.mount();
  return T;
}
const fire = (T, type, detail) => T.doc.body.dispatchEvent(
  new T.win.CustomEvent(type, { bubbles: true, cancelable: true, detail: detail || {} }));

(async function main() {
  // ── F-22: the banner says WHY a qubit is to be avoided ──────────────────
  {
    const T = world();
    const banner = T.doc.getElementById('topo-verdict-banner');
    const text = banner.textContent;
    ok(/structural issues · avoid \(below fail spec on Readout fidelity \(GE\), T1\): /.test(text),
       'F-22 the avoid list is separated from the structural count and names the failing metrics ('
       + text + ')');
    const chips = Array.from(banner.querySelectorAll('.verdict-avoid-chip'));
    ok(chips.map((c) => c.textContent).join(',') === 'q2,q4,q5', 'F-22 the three failing qubits are listed');
    const t4 = chips[1] && chips[1].getAttribute('title');
    ok(t4 === 'q4: Readout fidelity (GE) 87.90 % (fail < 90.00 %)',
       'F-22 each chip is titled with its failing value against the fail band (' + t4 + ')');
    const t2 = chips[0] && chips[0].getAttribute('title');
    ok(/Readout fidelity \(GE\) 88\.48 %/.test(t2) && /T1 5\.0 µs \(fail < 10\.0 µs\)/.test(t2),
       'F-22 a qubit failing two metrics names both (' + t2 + ')');
    const worst = T.doc.getElementById('topo-health-worst');
    const roChip = Array.from(worst.querySelectorAll('.worst-chip'))
      .filter((b) => /lowest readout fidelity \(GE\)/.test(b.textContent))[0];
    ok(!!roChip && roChip.getAttribute('data-inspect-id') === 'q4' && /87\.90%/.test(roChip.textContent)
       && roChip.classList.contains('fail'),
       'F-22 "Needs attention" lists the lowest readout fidelity, as the exported report does ('
       + (roChip && roChip.textContent) + ')');
  }

  // ── r2-04: Back/Forward leaves nothing running behind ───────────────────
  {
    const T = world();
    const base = { click: T.count('doc', 'click'), key: T.count('doc', 'keydown'),
                   bswap: T.count('body', 'htmx:beforeSwap'), diag: T.count('body', 'diagnostics-changed') };
    ok(base.key - T.pre.key === 1 && base.click - T.pre.click === 2 && base.diag === 1 && T.live.size === 1,
       'r2-04 setup: one mount = one keydown, two click handlers, one state listener, one live poller ('
       + JSON.stringify(base) + ' over app.js ' + JSON.stringify(T.pre) + ', intervals ' + T.live.size + ')');
    // Back / Forward x5: htmx restores the cached body -- the SERIALIZED DOM
    // it saved on leaving -- with no beforeSwap; the restored page's inline
    // script mounts again, then htmx:historyRestore fires
    for (let i = 0; i < 5; i++) {
      const snapshot = T.doc.body.innerHTML;
      T.doc.body.innerHTML = snapshot;
      T.mount();
      fire(T, 'htmx:historyRestore', { path: '/topology' });
    }
    const after = { click: T.count('doc', 'click'), key: T.count('doc', 'keydown'),
                    bswap: T.count('body', 'htmx:beforeSwap'), diag: T.count('body', 'diagnostics-changed') };
    ok(JSON.stringify(after) === JSON.stringify(base),
       'r2-04 listener counts stay flat over 5 restores (' + JSON.stringify(base) + ' -> ' + JSON.stringify(after) + ')');
    ok(T.live.size === 1, 'r2-04 exactly one live poller after the restores (' + T.live.size + ')');
    // the restored page's own mount was NOT swept: its handlers work
    const d = T.doc.querySelector('details.topo-report-export');
    d.open = true;
    fire(T, 'keydown');
    T.doc.body.dispatchEvent(new T.win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    ok(d.open === false, 'r2-04 the restored page keeps its own handlers (Escape still closes its Report menu)');
    // the restored Overview is wired again (a serialized "wired" marker used
    // to come back with the snapshot and leave + Add panel / the kebabs dead)
    T.doc.getElementById('ov-add-tile').dispatchEvent(new T.win.MouseEvent('click', { bubbles: true }));
    ok(!!T.doc.getElementById('ov-tile-popover'), 'r2-04 the restored Overview (+ Add panel) opens its popover');
    T.doc.body.dispatchEvent(new T.win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    // one state change -> one /api/topology
    const n0 = T.fetches.filter((u) => u.indexOf('/api/topology') === 0 && u.indexOf('mtime') < 0).length;
    fire(T, 'diagnostics-changed');
    await sleep(400);
    const n1 = T.fetches.filter((u) => u.indexOf('/api/topology') === 0 && u.indexOf('mtime') < 0).length;
    ok(n1 - n0 === 1, 'r2-04 one diagnostics-changed sends ONE /api/topology (' + (n1 - n0) + ')');
    // Back to a page without the dashboard: the poller stops
    T.doc.body.innerHTML = QUBITS_PAGE;
    fire(T, 'htmx:historyRestore', { path: '/qubits' });
    ok(T.live.size === 0, 'r2-04 Back to /qubits leaves no live poller (' + T.live.size + ')');
    ok(T.count('doc', 'keydown') === T.pre.key && T.count('doc', 'click') === T.pre.click
       && T.count('body', 'diagnostics-changed') === 0,
       'r2-04 ...and no Chip Status keydown / click / state listener');
    // an ordinary sidebar navigation (a #table-pane swap) still tears down
    T.doc.body.innerHTML = '<div id="table-pane">' + DASH + '</div>';
    T.mount();
    ok(T.live.size === 1 && T.count('body', 'diagnostics-changed') === 1, 'r2-04 re-mounted');
    fire(T, 'htmx:beforeSwap', { target: T.doc.getElementById('table-pane') });
    ok(T.live.size === 0 && T.count('body', 'diagnostics-changed') === 0 && T.count('doc', 'keydown') === T.pre.key,
       'r2-04 a #table-pane swap still runs every teardown');
    // a swap into some OTHER target leaves the live page alone
    T.mount();
    fire(T, 'htmx:beforeSwap', { target: T.doc.getElementById('topo-trends') });
    ok(T.live.size === 1 && T.count('body', 'diagnostics-changed') === 1,
       'r2-04 a swap into another target (a lazy section) tears nothing down');
  }

  // ── r2-14: the Report menu closes like every other popup ────────────────
  {
    const T = world();
    const d = T.doc.querySelector('details.topo-report-export');
    const esc = () => T.doc.body.dispatchEvent(
      new T.win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    d.open = true;
    esc();
    ok(d.open === false, 'r2-14 Escape closes the Report menu');
    d.open = true;
    T.doc.getElementById('topo-health-tiles').dispatchEvent(new T.win.MouseEvent('click', { bubbles: true }));
    ok(d.open === false, 'r2-14 a click outside closes it');
    d.open = true;
    T.doc.querySelector('.topo-report-menu').dispatchEvent(new T.win.MouseEvent('click', { bubbles: true }));
    ok(d.open === true, 'r2-14 a click inside the menu keeps it open');
    // the Overview's tile kebab stops propagation -- capture phase still sees it
    const kebab = T.doc.querySelector('#topo-overview-tiles .ov-tile-menu');
    kebab.dispatchEvent(new T.win.MouseEvent('click', { bubbles: true }));
    ok(!!T.doc.getElementById('ov-tile-popover') && d.open === false,
       'r2-14 opening a tile popover (a stopPropagation click) closes the menu: one overlay at a time');
    // innermost first: with both open, Escape closes the menu, then the popover
    d.open = true;
    esc();
    ok(d.open === false && !!T.doc.getElementById('ov-tile-popover'), 'r2-14 Escape closes the menu first...');
    esc();
    ok(!T.doc.getElementById('ov-tile-popover'), 'r2-14 ...and the popover on the next press');
    // picking a format closes it once the download has started
    d.open = true;
    const a = T.doc.getElementById('rep-md');
    ok(T.win.ChipStatus.reportHref(a, 'md') === true && /thresholds=/.test(a.href),
       'r2-14 the format link still carries the thresholds');
    await sleep(10);
    ok(d.open === false, 'r2-14 a picked format closes the menu');
    // the outside-click listener leaves with the page
    fire(T, 'htmx:beforeSwap', { target: T.doc.getElementById('table-pane') });
    ok(T.capClicks() === T.pre.cap,
       'r2-14 the capture-phase click listener is removed on navigation away');
  }

  console.log(fails ? ('FAILED ' + fails) : ('chip_status_qa3_selfcheck: all ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e); process.exit(1); });
