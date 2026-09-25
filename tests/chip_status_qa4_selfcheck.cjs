// QA round 2 (package cs-ui, chunk 4) -- against the REAL, mounted
// chip-status.js (+ app.js) under jsdom:
//
//   F-08              a bare /topology (and a bogus ?view=) lights Overview,
//                     the first section, in the tab strip AND the sidebar --
//                     not Topology, which stopped being first in docs/141 4o
//   chipstatus-r2-18  the Trends selection survives F5 / Back: every change
//                     stores the query it sent, the section's first build
//                     replays it (junk / foreign / unreadable storage -> the
//                     bare first-visit request), and the badge press order
//                     comes back with it
//   chipstatus-r2-20  the Panels and tile popovers take focus when they open,
//                     a keyboard change inside keeps it, an apply that closes
//                     the popover hands it to the rebuilt opener, and the
//                     inspector's x gives it back to the cell that opened it
//   chipstatus-r2-21  an open wiring-JSON sheet follows the inspected qubit
//                     and closes for a pair
//   chipstatus-r2-22  the pointer resting over another stone THROUGH the
//                     hover popup opens that stone's details; off the stones
//                     the popup stays, and a stone already showing is not
//                     re-opened
//
// Run: node tests/chip_status_qa4_selfcheck.cjs   (needs jsdom)
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
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const APP_JS = read('app.js'), TOPO_JS = read('topo-graph.js'), CS_JS = read('chip-status.js');
const WIRING_TPL = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_wiring.html'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The strip in the template's own order -- the page's first section is read
// from _wiring.html, so a future reorder cannot leave this pin stale.
const VIEWS = [];
WIRING_TPL.replace(/class="topo-subnav-btn" role="tab" data-view="([a-z0-9]+)"/g, (m, v) => { VIEWS.push(v); return m; });
const FIRST = VIEWS[0];
const STRIP = '<div class="topo-subnav">' + VIEWS.map((v) =>
  '<button type="button" class="topo-subnav-btn" data-view="' + v + '">' + v + '</button>').join('') + '</div>';
const SIDEBAR = '<nav class="sidebar-nav"><a href="/topology">Chip Status</a><ul class="nav-subitems" id="chip-status-subnav">'
  + VIEWS.map((v) => '<li><a href="/topology?view=' + v + '" data-view="' + v + '">' + v + '</a></li>').join('')
  + '</ul></nav>';
const DASH = '<div class="topo-dashboard">' + STRIP
  + '<div class="topo-section" data-topo-section="overview"><button id="ov-settings-btn">Panels</button>'
  + '<span id="ov-custom-note" hidden></span><div class="topo-summary-cards" id="topo-overview-tiles"></div></div>'
  + '<div class="topo-section topo-health-section" data-topo-section="health">'
  + '<div class="topo-verdict-banner" id="topo-verdict-banner" hidden></div>'
  + '<div class="topo-health-tiles" id="topo-health-tiles"></div>'
  + '<div class="topo-health-worst" id="topo-health-worst"></div></div>'
  + '<div class="topo-section" id="sec-topology"><div id="topo-hero"></div><div id="topo-html-wrap"></div></div>'
  + '<div class="topo-section" data-topo-section="trends"><div id="topo-trends"></div></div>'
  + '<div id="topo-metric-panels" data-topo-section="metrics"></div>'
  + '</div>'
  + '<div id="json-panel" class="json-panel hidden"><div class="json-panel-header">'
  + '<span id="json-panel-title">Wiring JSON</span></div><div id="json-panel-tree"></div></div>';
const PAGE = '<!DOCTYPE html><html><body>' + SIDEBAR + '<div id="table-pane">' + DASH + '</div>'
  + '<div id="inspector-pane"></div></body></html>';

function node(id, loc) {
  return { id: id, grid_location: loc, T1: 3e-5, gate_fidelity_avg: 0.998,
           metrics: { T1: { value: 3e-5 }, gate_fidelity_avg: { value: 0.998 } } };
}
const TOPO = {
  nodes: [node('q1', '0,0'), node('q2', '1,0'), node('q3', '2,0')],
  edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.99, metrics: {} }],
  summary: {},
};
const RAW = { wiring: { qubits: { q1: { xy: { opx_output: '#/ports/a' } }, q2: { xy: { opx_output: '#/ports/b' } } } } };
const META = { T1: { label: 'T1', abbr: 'T1', direction: 'higher' },
               gate_fidelity_avg: { label: '1Q gate fidelity', abbr: 'Gate F', direction: 'higher' } };

/* opts: url, chipView, storage (object preset), histState, chipToken */
function world(opts) {
  opts = opts || {};
  const dom = new JSDOM(PAGE, { runScripts: 'outside-only', pretendToBeVisual: true,
                               url: opts.url || 'http://localhost/topology' });
  const win = dom.window;
  if (opts.histState) win.history.replaceState(opts.histState, '');
  const T = { win: win, doc: win.document, ajax: [] };
  // base.html sets it inline, before any script, from the render-time chip
  if (opts.chipToken != null) win.__chipToken = opts.chipToken;
  if (opts.storage) {
    Object.keys(opts.storage).forEach((k) => win.localStorage.setItem(k, opts.storage[k]));
  }
  win.htmx = { ajax: function (m, u) { T.ajax.push(u); return win.Promise.resolve(); }, process: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
  win.Element.prototype.scrollIntoView = function () {};
  win.Plotly = {};
  win._plotlyRender = function () { return new win.Promise(function () {}); };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  win.ChipStatus.mount({ topo: JSON.parse(JSON.stringify(TOPO)), rawWiring: RAW, defaultThresholds: {},
                         diagFindings: [], metricMeta: META,
                         chipView: opts.chipView == null ? '' : opts.chipView });
  // a private window throws on ACCESS
  T.privateWindow = () => Object.defineProperty(win, 'localStorage', {
    get: function () { throw new Error('SecurityError'); } });
  T.lit = () => {
    const a = T.doc.querySelector('.topo-subnav-btn.active');
    const s = T.doc.querySelector('#chip-status-subnav a.active');
    return (a && a.getAttribute('data-view')) + '|' + (s && s.getAttribute('data-view'));
  };
  return T;
}
const click = (T, el) => el.dispatchEvent(new T.win.MouseEvent('click', { bubbles: true, cancelable: true }));
const esc = (T) => T.doc.activeElement.dispatchEvent(
  new T.win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));

(async function main() {
  // ── F-08: the top of the page is Overview ───────────────────────────────
  {
    ok(FIRST === 'overview' && VIEWS.indexOf('topology') > 0,
       'F-08 setup: _wiring.html\'s strip starts with ' + FIRST + ' (' + VIEWS.join(',') + ')');
    for (const cv of ['', 'bogus']) {
      const T = world({ chipView: cv });
      ok(T.lit() === FIRST + '|' + FIRST,
         'F-08 ?view=' + (cv || '(none)') + ' lights the first section in the strip and the sidebar (' + T.lit() + ')');
    }
    // Back / F5 onto an entry recorded above the first section (QA F-20's
    // restore path) lands on the same place, so it lights the same tab
    const H = world({ histState: { smChipScroll: { url: '/topology', view: null, top: 0, d: 0 } } });
    ok(H.lit() === FIRST + '|' + FIRST, 'F-08 a restore above the first section lights it too (' + H.lit() + ')');
    // a real deep link still wins
    const D = world({ chipView: 'trends', url: 'http://localhost/topology?view=trends' });
    ok(D.lit() === 'trends|trends', 'F-08 a valid deep link still lights its own section (' + D.lit() + ')');
    // app.js's sidebar matcher agrees on a bare URL
    const S = world({ url: 'http://localhost/topology' });
    S.win.syncSidebarNavActive();
    const act = Array.from(S.doc.querySelectorAll('.sidebar-nav a.active')).map((a) => a.getAttribute('href'));
    ok(act.join(',') === '/topology,/topology?view=' + FIRST,
       'F-08 syncSidebarNavActive on a bare /topology lights Chip Status + ' + FIRST + ' (' + act.join(',') + ')');
  }

  // ── r2-18: the Trends selection survives a reload ───────────────────────
  {
    const KEY = 'quam_trends_sel_v1';
    const SECTION = '<div class="topo-trends" id="topo-trends"><div class="topo-trends-controls">'
      + '<button class="topo-trend-chip active" data-trend-metric="T1">T1</button>'
      + '<button class="topo-trend-chip" data-trend-metric="f_01">f01</button>'
      + '<input id="topo-trend-path" value=""><div id="topo-trend-suggest" hidden></div></div>'
      + '<div class="topo-trends-2q">'
      + '<button class="topo-trend-chip topo-trend-badge" data-trend-path="qubit_pairs.*.B">B</button>'
      + '<button class="topo-trend-chip topo-trend-badge" data-trend-path="qubit_pairs.*.A">A</button>'
      + '</div><div class="topo-trends-grid"></div></div>';
    const T = world();
    T.doc.getElementById('topo-trends').outerHTML = SECTION;
    T.win.ChipTrends.toggle('f_01');
    T.win.ChipTrends.togglePath('qubit_pairs.*.A');
    T.doc.getElementById('topo-trend-path').value = 'x180';
    T.win.ChipTrends.setPath('x180');
    const sent = T.ajax[T.ajax.length - 1];
    const stored = T.win.localStorage.getItem(KEY);
    ok(stored && sent === '/topology/trends?' + stored && /metrics=T1%2Cf_01/.test(stored)
       && /paths=qubit_pairs\.\*\.A/.test(stored) && /path=x180/.test(stored),
       'r2-18 every change stores exactly the query it sent (' + stored + ')');
    // everything off is a choice too, and still starts with metrics=
    T.win.ChipTrends.toggle('T1'); T.win.ChipTrends.toggle('f_01');
    ok(/^metrics=(&|$)/.test(T.win.localStorage.getItem(KEY)),
       'r2-18 all chips off is stored as an empty metrics= (' + T.win.localStorage.getItem(KEY) + ')');

    // a fresh page (F5 / Back) builds the section from what was stored
    const Q = 'metrics=T2echo&paths=' + encodeURIComponent('qubit_pairs.*.A,qubit_pairs.*.B') + '&path=x180';
    const R = world({ storage: { [KEY]: Q } });
    R.win.setChipStatusView('trends', null, false);
    const first = R.ajax.filter((u) => u.indexOf('/topology/trends') === 0);
    ok(first[0] === '/topology/trends?' + Q, 'r2-18 the first build replays the stored selection (' + first[0] + ')');
    // ...and the press order came back: the server renders the badges in its
    // own order (B before A); A was pressed last, so A still leads
    R.doc.getElementById('topo-trends').outerHTML = SECTION.replace(/topo-trend-badge"/g, 'topo-trend-badge active"');
    R.win.ChipTrends.toggle('T1');
    const u = decodeURIComponent(R.ajax[R.ajax.length - 1]);
    ok(u.indexOf('paths=qubit_pairs.*.A,qubit_pairs.*.B') > 0,
       'r2-18 the badge press order is restored, so the families cap trims the same badge (' + u + ')');

    for (const [label, o] of [['nothing stored', {}],
                              ['a foreign value', { storage: { [KEY]: 'garbage=1' } }],
                              ['an oversize value', { storage: { [KEY]: 'metrics=' + 'x'.repeat(5000) } }],
                              ['a private window', { throwStorage: true }]]) {
      const W = world(o);
      if (o.throwStorage) { await sleep(20); W.privateWindow(); }   // after app.js's own startup reads
      W.win.setChipStatusView('trends', null, false);
      const f = W.ajax.filter((x) => x.indexOf('/topology/trends') === 0)[0];
      ok(f === '/topology/trends', 'r2-18 ' + label + ' -> the bare first-visit request (' + f + ')');
    }

    // review: the selection belongs to the CHIP it was made on
    const A = world({ chipToken: 'chipA' });
    A.doc.getElementById('topo-trends').outerHTML = SECTION;
    A.win.ChipTrends.togglePath('qubit_pairs.*.A');
    const kA = A.win.localStorage.getItem(KEY + '::chipA');
    ok(kA && /paths=qubit_pairs\.\*\.A/.test(kA) && A.win.localStorage.getItem(KEY) === null,
       'r2-18 review: a selection made on chip A is stored under chip A (' + kA + ')');
    // the browser's storage as chip A left it, whatever key A chose
    const left = {};
    for (let i = 0; i < A.win.localStorage.length; i++) {
      const k = A.win.localStorage.key(i); left[k] = A.win.localStorage.getItem(k);
    }
    const B = world({ chipToken: 'chipB', storage: left });
    B.win.setChipStatusView('trends', null, false);
    const fb = B.ajax.filter((x) => x.indexOf('/topology/trends') === 0)[0];
    ok(fb === '/topology/trends',
       'r2-18 review: chip B\'s first Trends build does not replay chip A\'s badges / typed family (' + fb + ')');
    const A2 = world({ chipToken: 'chipA', storage: left });
    A2.win.setChipStatusView('trends', null, false);
    const fa = A2.ajax.filter((x) => x.indexOf('/topology/trends') === 0)[0];
    ok(fa === '/topology/trends?' + kA, 'r2-18 review: ...while chip A gets its own back (' + fa + ')');
  }

  // ── r2-20: focus goes into the popovers and comes back ──────────────────
  {
    const T = world();
    const d = T.doc;
    const btn = d.getElementById('ov-settings-btn');
    btn.focus();
    T.win._ovOpenSettings(btn);
    const sp = d.getElementById('ov-settings-pop');
    ok(sp && d.activeElement === sp, 'r2-20 opening Panels moves focus INTO the popover (' + d.activeElement.id + ')');
    ok(sp.getAttribute('tabindex') === '-1', 'r2-20 ...onto the popover itself, so a second Enter presses nothing');
    // a keyboard change of a per-tile statistic re-renders the body
    const sel = sp.querySelector('.ov-set-sel');
    const tid = sel.getAttribute('data-tile-id');
    sel.focus();
    sel.value = 'max';
    sel.dispatchEvent(new T.win.Event('change', { bubbles: true }));
    const a1 = d.activeElement;
    ok(a1 !== sel && a1.classList.contains('ov-set-sel') && a1.getAttribute('data-tile-id') === tid && sp.contains(a1),
       'r2-20 a keyboard change keeps focus on the SAME control after the re-render ('
       + a1.tagName + ' ' + a1.getAttribute('data-tile-id') + ')');
    const gb = sp.querySelector('[data-global-stat="median"]');
    gb.focus();
    click(T, gb);
    ok(d.activeElement.getAttribute('data-global-stat') === 'median' && sp.contains(d.activeElement),
       'r2-20 ...and on the same segment button after "for ALL panels"');
    esc(T);
    ok(!d.getElementById('ov-settings-pop') && d.activeElement === d.getElementById('ov-settings-btn'),
       'r2-20 Escape closes it and focus is back on Panels');

    // a tile's kebab
    const menu = d.querySelector('#topo-overview-tiles .topo-card:not([data-tile-composite]) .ov-tile-menu');
    const tileId = menu.getAttribute('data-tile-id');
    menu.focus();
    click(T, menu);
    const tp = d.getElementById('ov-tile-popover');
    ok(tp && d.activeElement === tp, 'r2-20 opening a tile\'s ⋮ moves focus into its popover');
    esc(T);
    ok(!d.getElementById('ov-tile-popover') && d.activeElement === menu, 'r2-20 Escape hands it back to the ⋮');
    // an apply closes the popover and rebuilds every tile: focus goes to the
    // REBUILT kebab of the same tile, not to <body>
    const m2 = d.querySelector('.ov-tile-menu[data-tile-id="' + tileId + '"]');
    click(T, m2);
    const st = d.getElementById('ov-pop-stat');
    if (st) {
      st.focus();
      st.value = 'min';
      st.dispatchEvent(new T.win.Event('change', { bubbles: true }));
      const a2 = d.activeElement;
      ok(!d.getElementById('ov-tile-popover') && a2 && a2.classList.contains('ov-tile-menu')
         && a2.getAttribute('data-tile-id') === tileId && a2 !== m2 && a2.isConnected,
         'r2-20 a keyboard apply hands focus to the rebuilt ⋮ of the same tile (' + (a2 && a2.tagName) + ')');
    } else {
      ok(false, 'r2-20 setup: tile ' + tileId + ' has a statistic select');
    }

    // the inspector's x
    const q2 = d.querySelector('[data-hero-qubit="q2"]');
    click(T, q2);                                          // the mouse opens it
    const pane = d.getElementById('inspector-pane');
    pane.innerHTML = '<h2>Qubit q2</h2><button class="inspector-close">x</button><input id="insp-field">';
    pane.querySelector('.inspector-close').focus();
    T.win.closeInspector();                                // what the x's onclick runs
    ok(d.activeElement === q2 && q2.getAttribute('tabindex') === '0',
       'r2-20 the inspector\'s x gives focus back to the stone that opened it (' + d.activeElement.tagName + ')');
    // never stolen from somewhere the user moved to
    click(T, q2);
    pane.innerHTML = '<button class="inspector-close">x</button>';
    const other = d.getElementById('ov-settings-btn');
    other.focus();
    T.win.closeInspector();
    ok(d.activeElement === other, 'r2-20 a close while focus is elsewhere leaves it there');
    // gone with the page
    T.doc.body.dispatchEvent(new T.win.CustomEvent('htmx:beforeSwap', { bubbles: true, detail: { target: d.getElementById('table-pane') } }));
    pane.innerHTML = '<button class="inspector-close">x</button>';
    pane.querySelector('button').focus();
    T.win.closeInspector();
    ok(d.activeElement === d.body, 'r2-20 the listener leaves with the page (nothing re-focused after a swap)');
  }

  // ── r2-21: the wiring-JSON sheet follows the inspector ──────────────────
  {
    const T = world();
    const d = T.doc;
    const jp = d.getElementById('json-panel');
    const title = () => d.getElementById('json-panel-title').textContent;
    T.win._inspectQubit('q1');
    ok(jp.classList.contains('hidden'), 'r2-21 inspecting with the sheet closed does not open it');
    const q2 = d.querySelector('[data-hero-qubit="q2"]');
    click(T, q2); click(T, q2);                            // double-click
    ok(!jp.classList.contains('hidden') && /— q2$/.test(title()), 'r2-21 setup: a double-click opens q2\'s sheet (' + title() + ')');
    T.win._inspectQubit('q1');
    ok(!jp.classList.contains('hidden') && /— q1$/.test(title()) && /ports\/a/.test(d.getElementById('json-panel-tree').textContent),
       'r2-21 inspecting q1 moves the open sheet to q1 (' + title() + ')');
    T.win._inspectPair('q1-q2');
    ok(jp.classList.contains('hidden'), 'r2-21 inspecting a pair closes the qubit sheet');
    ok(T.ajax.filter((u) => u.indexOf('/pair/') === 0).length === 1, 'r2-21 ...and still inspects the pair');
  }

  // ── r2-22: hovering from one stone to the next THROUGH the popup ────────
  {
    const T = world();
    const d = T.doc, win = T.win;
    const stone = (id) => d.querySelector('[data-hero-qubit="' + id + '"]');
    const heads = () => Array.from(d.querySelectorAll('.topo-card-popup .topo-popup-header span')).map((s) => s.textContent);
    stone('q1').dispatchEvent(new win.MouseEvent('mouseenter'));
    await sleep(320);
    const p1 = d.querySelector('.topo-card-popup');
    ok(p1 && heads().join() === 'q1 — details', 'r2-22 setup: hovering q1 opens its popup (' + heads() + ')');
    // what lies under the pointer, top first: the popup, then the map
    let under = [];
    d.elementsFromPoint = function () { return [p1.firstChild, p1].concat(under); };
    const svg = d.querySelector('#topo-hero svg');
    stone('q1').dispatchEvent(new win.MouseEvent('mouseleave'));
    under = [svg];
    p1.dispatchEvent(new win.MouseEvent('mouseenter'));
    p1.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 10, clientY: 10 }));
    await sleep(320);
    ok(d.querySelector('.topo-card-popup') === p1 && heads().join() === 'q1 — details',
       'r2-22 off the stones the popup is the popup: it stays');
    under = [stone('q1').querySelector('circle') || stone('q1'), stone('q1'), svg];
    p1.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 11, clientY: 10 }));
    await sleep(320);
    ok(d.querySelector('.topo-card-popup') === p1, 'r2-22 over its OWN stone nothing changes');
    // the pointer passes over q2 but moves on before the intent delay
    under = [stone('q2').querySelector('circle') || stone('q2'), stone('q2'), svg];
    p1.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 20, clientY: 10 }));
    await sleep(100);
    under = [svg];
    p1.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 30, clientY: 10 }));
    await sleep(320);
    ok(d.querySelector('.topo-card-popup') === p1, 'r2-22 passing over q2 without resting does not switch');
    // ...and resting on q2 through the popup does
    under = [stone('q2').querySelector('circle') || stone('q2'), stone('q2'), svg];
    p1.dispatchEvent(new win.MouseEvent('mousemove', { clientX: 40, clientY: 10 }));
    await sleep(320);
    const p2 = d.querySelector('.topo-card-popup');
    ok(d.querySelectorAll('.topo-card-popup').length === 1 && heads().join() === 'q2 — details',
       'r2-22 resting on q2 through q1\'s popup replaces it with q2\'s details (' + heads() + ')');
    // the stone now under the pointer does not re-open what is already up
    stone('q2').dispatchEvent(new win.MouseEvent('mouseenter'));
    await sleep(320);
    ok(d.querySelector('.topo-card-popup') === p2, 'r2-22 entering the stone whose popup is up keeps it (no re-open)');
    // leaving still closes it
    stone('q2').dispatchEvent(new win.MouseEvent('mouseleave'));
    await sleep(320);
    ok(!d.querySelector('.topo-card-popup'), 'r2-22 leaving closes it as before');
  }

  console.log(fails ? ('FAILED ' + fails) : ('chip_status_qa4_selfcheck: all ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e); process.exit(1); });
