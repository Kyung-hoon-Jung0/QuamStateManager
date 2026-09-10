// The typeahead, driven against the REAL shipped file under jsdom.
//
// Customer, 2026-09-10: type `m` and see the parameters that start with m,
// YouTube/VS Code style; and in Live Edit / Json Tree, type `ampl` and see
// every amplitude key.
//
// What is driven here is the part a screenshot cannot show: what happens
// BETWEEN keystrokes. The real-browser pass (recorded in the commit) covers
// placement and the three real pages; this covers the state machine.
//
// The world is built LATE on purpose — the scripts are evaluated first and the
// DOM after — because this file evaluates in <head> before the sidebar exists,
// and a pre-built-DOM harness is exactly what hid the docs/149 dead binding.
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
const SQ_JS = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');
const TH_JS = fs.readFileSync(path.join(STATIC, 'sidebar-typeahead.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

// `num_shots` is FIRST on purpose: it contains an m but does not start with
// one, so a rank that treats substring and prefix alike puts it above
// `multiplexed` -- which is exactly the ask ("m을 치면 m으로 시작하는") failing.
// Without a substring-before-prefix key in the source order the two ranks are
// indistinguishable and the mutation survives.
const VOCAB = {
  v: 7, n_runs: 100, omitted: 0, hydrating: false,
  keys: [
    { k: 'num_shots', n: 99, v: [['2000', 99]], more: 0 },
    { k: 'multiplexed', n: 90, v: [['false', 70], ['true', 20]], more: 0 },
    { k: 'min_wait_time_in_ns', n: 40, v: [['16', 40]], more: 0 },
    { k: 'max_amp_factor', n: 30, v: [['1.5', 20], ['2.0', 10]], more: 3 },
    { k: 'reset_type', n: 80, v: [['thermal', 60], ['active', 20]], more: 0 },
  ],
};

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><head></head><body></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  global.CSS = win.CSS;                 // docs/125: bridge, or it throws
  win.htmx = { ajax() {}, trigger() {}, process() {} };
  win.fetches = [];
  win.fetch = function (u) {
    win.fetches.push(u);
    return Promise.resolve({ status: 200, ok: true, json: function () { return Promise.resolve(VOCAB); } });
  };
  win.anchored = [];
  win._anchorPopover = function (p, b) { win.anchored.push([p.id, b && b.id]); };

  // Scripts FIRST, DOM after — the real load order.
  new win.Function(SQ_JS).call(win, win);
  new win.Function(TH_JS).call(win, win);

  win.document.body.innerHTML =
    '<textarea id="sidebar-filter-input"></textarea>'
    + '<input type="search" id="bulk-search">'
    + '<input type="text" id="explorer-search">'
    + '<div id="table-pane"><table><thead><tr>'
    + '<th class="bulk-col-head" data-col-key="x180_amplitude" data-section="XY Drive">'
    + '<span class="bulk-col-label">x180 amp</span></th>'
    + '<th class="bulk-col-head" data-col-key="ro_amplitude" data-section="Readout">'
    + '<span class="bulk-col-label">RO amp</span></th>'
    + '<th class="bulk-col-head" data-col-key="xy_sampling_rate" data-section="XY Port">'
    + '<span class="bulk-col-label">XY samp rate</span></th>'
    + '</tr></thead></table></div>'
    + '<div id="explorer-tree-state"></div><div id="explorer-tree-wiring"></div>';
  win.document.getElementById('explorer-tree-state')._treeData = {
    qubits: { q1: { xy: { operations: { x180: { amplitude: 0.1 } } },
                    resonator: { operations: { readout: { amplitude: 0.2 } } } } },
    twpas: { t1: { pump_amplitude: 1, isolation_amplitude: 2 } },
  };
  win.document.getElementById('explorer-tree-wiring')._treeData = {
    wiring: { qubits: { q1: { xy: { opx_output: '#/ports/a' } } } },
    ports: { mw_outputs: { con1: { '1': { sampling_rate: 1e9, confusion: [[1, 2], [3, 4]] } } } },
  };
  return win;
}

function type(win, id, value, caret) {
  const el = win.document.getElementById(id);
  el.value = value;
  el.selectionStart = el.selectionEnd = (caret == null ? value.length : caret);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  return el;
}
function rows(win) {
  const p = win.document.getElementById('sm-typeahead');
  if (!p || p.hidden) return [];
  return Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function (r) {
    return r.querySelector('.sm-th-label').textContent
         + (r.classList.contains('sm-th-note') ? ' (note)' : '')
         + (r.classList.contains('active') ? ' <=' : '');
  });
}
/* The rows, with the marks that say "this is a guess". A fuzzy row's label
   text is the EXACT key (the approximately-equal sign is a CSS ::before), which
   is the property that lets accepting one insert something real. */
function marked(win) {
  const p = win.document.getElementById('sm-typeahead');
  if (!p || p.hidden) return [];
  return Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function (r) {
    return r.querySelector('.sm-th-label').textContent
         + (r.classList.contains('sm-th-fuzzsep') ? ' (sep)' : '')
         + (r.classList.contains('sm-th-fuzzy') ? ' (fuzzy)' : '')
         + (r.classList.contains('sm-th-note') ? ' (note)' : '');
  });
}
function inserts(win) {
  const st = win.Typeahead._state();
  return st ? st.items.map(function (i) { return i.insert == null ? null : i.insert; }) : [];
}
function keydown(win, id, key) {
  const ev = new win.KeyboardEvent('keydown', { key: key, bubbles: true, cancelable: true });
  win.document.getElementById(id).dispatchEvent(ev);
  return ev;
}

(async function () {
  const win = world();
  ok(typeof win.Typeahead === 'object', 'A0 the widget loaded');
  ok(typeof win.SidebarTypeahead === 'object'
     && typeof win.BulkTypeahead === 'object'
     && typeof win.TreeTypeahead === 'object', 'A1 all three consumers loaded');

  /* ── A. the sidebar: bound at DOCUMENT level, so a DOM built after the
        script still gets the panel (docs/149's dead-binding shape) ───── */
  type(win, 'sidebar-filter-input', 'm');
  await new Promise(function (r) { setTimeout(r, 20); });
  ok(rows(win).length === 1 && /loading/.test(rows(win)[0]),
     'A2 first keystroke asks for the vocabulary and says so: ' + rows(win));
  ok(win.fetches.length === 1 && /param-vocab/.test(win.fetches[0]),
     'A3 …exactly one request, for the vocabulary');
  await new Promise(function (r) { setTimeout(r, 30); });

  type(win, 'sidebar-filter-input', 'm');
  ok(rows(win)[0] === 'multiplexed',
     'A4 `m` puts the PREFIX match first, not merely a substring one: ' + rows(win));
  // four of the five fixture keys contain an m; reset_type does not.
  ok(rows(win).length === 4, 'A5 …and lists every m key: ' + rows(win));
  ok(rows(win).indexOf('num_shots') > rows(win).indexOf('multiplexed'),
     'A5b …with the substring-only match BELOW the prefix ones: ' + rows(win));
  ok(win.fetches.length === 1, 'A6 typing costs no further request');

  type(win, 'sidebar-filter-input', 'mult');
  ok(rows(win).length === 1 && rows(win)[0] === 'multiplexed',
     'A7 `mult` narrows to one: ' + rows(win));

  /* ── B. Enter is NOT redefined ────────────────────────────────────── */
  const ev1 = keydown(win, 'sidebar-filter-input', 'Enter');
  ok(ev1.defaultPrevented === false,
     'B1 with nothing selected Enter falls through — the box keeps its meaning');
  ok(win.document.getElementById('sidebar-filter-input').value === 'mult',
     'B2 …and nothing was inserted');

  keydown(win, 'sidebar-filter-input', 'ArrowDown');
  ok(/<=/.test(rows(win).join('')), 'B3 ArrowDown activates a row');
  keydown(win, 'sidebar-filter-input', 'ArrowUp');
  ok(!/<=/.test(rows(win).join('')), 'B4 …and ArrowUp comes back off the list (no wrap)');

  /* ── C. two stages: a key alone matches nothing, so it is never fired ─ */
  keydown(win, 'sidebar-filter-input', 'ArrowDown');
  const before = win.htmxTriggered;
  keydown(win, 'sidebar-filter-input', 'Enter');
  const el = win.document.getElementById('sidebar-filter-input');
  ok(el.value === 'multiplexed=',
     'C1 accepting a KEY leaves `key=`, an intermediate state: ' + el.value);
  ok(rows(win).join(',') === 'false,true',
     'C2 …and the VALUES appear at once: ' + rows(win));
  ok(el.selectionStart === 'multiplexed='.length, 'C3 the caret is after the =');

  keydown(win, 'sidebar-filter-input', 'ArrowDown');
  keydown(win, 'sidebar-filter-input', 'Enter');
  ok(el.value === 'multiplexed=false',
     'C4 accepting a VALUE completes the token: ' + el.value);
  ok(rows(win).length === 0,
     'C5 …and the panel does NOT re-suggest the value just accepted');

  /* ── D. the token under the caret, not the whole box ───────────────── */
  type(win, 'sidebar-filter-input', 'rabi mult', 9);
  ok(rows(win)[0] === 'multiplexed', 'D1 completes the token being finished');
  const el2 = win.document.getElementById('sidebar-filter-input');
  keydown(win, 'sidebar-filter-input', 'ArrowDown');
  keydown(win, 'sidebar-filter-input', 'Enter');
  ok(el2.value === 'rabi multiplexed=',
     'D2 …and splices it in place, leaving the rest alone: ' + el2.value);

  type(win, 'sidebar-filter-input', 'rabi ', 5);
  ok(rows(win).length === 0, 'D3 a token not begun is not completed');
  type(win, 'sidebar-filter-input', 'a "b c', 5);
  ok(rows(win).length === 0, 'D4 an unbalanced quote refuses rather than guessing');

  /* ── E. Escape, and typing on ─────────────────────────────────────── */
  type(win, 'sidebar-filter-input', 'mult');
  ok(rows(win).length === 1, 'E1 open');
  keydown(win, 'sidebar-filter-input', 'Escape');
  ok(rows(win).length === 0, 'E2 Escape closes');
  type(win, 'sidebar-filter-input', 'mult');
  ok(rows(win).length === 0, 'E3 …and it stays closed on the SAME token');
  type(win, 'sidebar-filter-input', 'multi');
  ok(rows(win).length === 1, 'E4 …but typing on is a new token, so it opens again');

  /* ── F. Live State Edit: WORDS, because this box splits on whitespace ─ */
  type(win, 'bulk-search', 'ampl');
  ok(rows(win).indexOf('amplitude') >= 0,
     'F1 `ampl` offers amplitude on the grid: ' + rows(win));
  ok(rows(win).indexOf('x180 amp') < 0,
     'F2 …and NOT the multi-word label — this box tokenizes on /\\s+/ with no '
     + 'quote handling, so a label would silently become three AND-ed tokens');
  const bulk = win.document.getElementById('bulk-search');
  keydown(win, 'bulk-search', 'ArrowDown');
  keydown(win, 'bulk-search', 'Enter');
  ok(bulk.value === 'amplitude', 'F3 accepting inserts one safe token: ' + bulk.value);

  /* ── G. Json Tree: the key names the chip really has ───────────────── */
  type(win, 'explorer-search', 'ampl');
  const g = rows(win);
  ok(g.indexOf('amplitude') >= 0 && g.indexOf('pump_amplitude') >= 0
     && g.indexOf('isolation_amplitude') >= 0,
     'G1 `ampl` finds every amplitude key, state AND twpa: ' + g);
  ok(g.indexOf('sampling_rate') >= 0,
     'G2 …including one from the WIRING tree — both containers are read: ' + g);
  const v = win.TreeTypeahead.vocab();
  ok(!Object.keys(v).some(function (k) { return /^\d+$/.test(k); }),
     'G3 a list index is not a key anyone types (confusion.0.1 gives no "0")');
  ok(v.amplitude === 2, 'G4 the count is places, not occurrences: ' + v.amplitude);

  /* ── I. a tree swap must not dismiss it ────────────────────────────
        This box fires /workspace/tree on a 250 ms debounce, so EVERY keystroke
        swaps #sidebar-tree. A blanket close on htmx swaps dismissed the panel
        ~250 ms after it opened, every time — the panel was correct and
        invisible, and only a real browser showed it. */
  type(win, 'sidebar-filter-input', 'mult');
  ok(rows(win).length === 1, 'I1 open before the swap');
  win.document.dispatchEvent(new win.CustomEvent('htmx:afterSwap',
    { bubbles: true, detail: {} }));
  ok(rows(win).length === 1, 'I2 …and still open after one: ' + rows(win));

  /* ── J. the plain boxes do not inherit the param grammar ───────────
        `classify` would read `amp=x` as a key/value pair and complete VALUES of
        a key called `amp`; on a box whose grammar is AND-ed words that is a
        different query from the one the user is typing. */
  // `p:ampl` is the case that TELLS THEM APART. The param classifier reads
  // `p:` as a scope and completes on `ampl`; the plain one sees one word with a
  // colon in it, which no column and no chip key is called. An `amp=x` case
  // cannot distinguish them -- both end up with a stem that matches nothing.
  type(win, 'explorer-search', 'p:ampl');
  ok(rows(win).length === 0,
     'J1 a `scope:`-looking word is ONE plain token on the tree box, not a '
     + 'scope to complete inside: ' + rows(win));
  type(win, 'bulk-search', 'p:ampl');
  ok(rows(win).length === 0,
     'J2 …and the same on the grid: ' + rows(win));
  // …while the plain stem still completes normally
  type(win, 'explorer-search', 'ampl');
  ok(rows(win).indexOf('amplitude') >= 0, 'J3 the ordinary word still works');

  /* ── H. placement is the app's one body-level popup ────────────────── */
  ok(win.anchored.length > 0 && win.anchored[0][0] === 'sm-typeahead',
     'H1 anchored through _anchorPopover, never positioned by hand');
  ok(win.document.getElementById('sm-typeahead').parentElement === win.document.body,
     'H2 …and it lives on <body>: #sidebar is overflow-y:auto and would clip it');

  /* ── K. a typo still finds the key ─────────────────────────────────
   *
   * Customer, 2026-09-10: "특히 파라미터를 입력하면 사실 많은 사람들이
   * multiplzed...뭐 이런식으로 오타 나잖아?"
   *
   * That word is the whole reason the algorithm is what it is, so it is the
   * fixture. Everything else in this section exists to stop the guess from
   * being mistaken for a match. */
  type(win, 'sidebar-filter-input', 'multiplzed');
  const k = marked(win);
  ok(k.indexOf('multiplexed (fuzzy)') >= 0,
     'K1 the customer\'s own typo finds the key: ' + k);
  ok(k.filter(function (r) { return / \(sep\)/.test(r); }).length === 1
     && k.indexOf('multiplexed (fuzzy)') > k.findIndex(function (r) { return / \(sep\)/.test(r); }),
     'K2 …under a separator that says it is a guess, never mixed in: ' + k);
  ok(inserts(win).indexOf('multiplexed=') >= 0,
     'K3 accepting a guess inserts the REAL key, never the typo: ' + inserts(win));
  ok('multiplexed'.indexOf('z') < 0,
     'K4 the reason it cannot be a subsequence matcher: there is no z in the target');

  // the gates, called directly -- the vocabulary here is too small to reach
  // three honest hits on a four-character stem any other way
  // `wiat_time` is the near miss: one transposition from the stem, and NOT a
  // substring hit — so it is a thing the pass would offer if the gate let it.
  const GATE = ['wait_a', 'wait_b', 'wait_c', 'wiat_time'];
  ok(win.Typeahead.rank(GATE, 'wait').fuzz.length === 0,
     'K5 with three honest hits, nothing is guessed — a guess is for when the '
     + 'box would otherwise be empty');
  ok(win.Typeahead.rank(['wait_a', 'wiat_time'], 'wait').fuzz.join() === 'wiat_time',
     'K5b …and with only one honest hit, the same near miss IS offered — '
     + 'which is what makes K5 a test of the gate and not of the fixture');
  const r5 = win.Typeahead.rank(['amplitude'], 'amplitide');
  ok(r5.fuzz.length === 1 && r5.pre.length === 0 && r5.sub.length === 0,
     'K6 …and with none, the near miss is offered: ' + JSON.stringify(r5));
  const r6 = win.Typeahead.rank(['multiplexed'], 'mul');
  ok(r6.fuzz.length === 0,
     'K7 below four characters a miss is a miss — a three-letter stem is not a typo');
  const r7 = win.Typeahead.rank(['multiplexed'], 'zzzzzzzzzz');
  ok(r7.fuzz.length === 0, 'K8 a word with nothing in common is not "close"');

  ok(win.Typeahead.prefixDist('multiplzed', 'multiplexed', 2) === 2,
     'K9 distance 2 — which is why a distance-1 cap misses the reported word');
  ok(win.Typeahead.prefixDist('mutliplexed', 'multiplexed', 2) === 1,
     'K10 a transposition is ONE edit (OSA), not two');
  ok(win.Typeahead.prefixDist('multi', 'multiplexed', 2) === 0,
     'K11 distance is to a PREFIX, so a stem mid-typing is not penalised for '
     + 'the rest of the name');
  ok([0, 1, 6, 7, 10].map(function (n) { return win.Typeahead.maxEdits(n); })
       .join(',') === '0,0,1,2,2',
     'K12 the edit budget grows with the stem: '
     + [0, 1, 6, 7, 10].map(function (n) { return win.Typeahead.maxEdits(n); }));

  /* Arrow keys must not stop on the separator — it is a line of prose. */
  type(win, 'sidebar-filter-input', 'multiplzed');
  keydown(win, 'sidebar-filter-input', 'ArrowDown');
  const st = win.Typeahead._state();
  ok(st && st.active >= 0 && !st.items[st.active].note,
     'K13 ArrowDown lands on something selectable, never on the separator');

  /* The value stage gets it too — a value is as typo-prone as a key. */
  type(win, 'sidebar-filter-input', 'multiplexed=flase');
  ok(marked(win).indexOf('false (fuzzy)') >= 0,
     'K14 a mistyped VALUE finds the real one: ' + marked(win));

  /* …and so do the two plain boxes. */
  type(win, 'explorer-search', 'amplitide');
  ok(marked(win).indexOf('amplitude (fuzzy)') >= 0,
     'K15 the Json Tree box guesses too: ' + marked(win));
  type(win, 'bulk-search', 'amplitide');
  ok(marked(win).indexOf('amplitude (fuzzy)') >= 0,
     'K16 …and Live State Edit: ' + marked(win));

  /* ── L. the two per-keystroke defects (A) put weight on ─────────────── */
  const b1 = win.BulkTypeahead.vocab();
  const b2 = win.BulkTypeahead.vocab();
  ok(b1 === b2,
     'L1 the grid vocabulary is CACHED — it was a querySelectorAll plus a '
     + 'regex split per header on every keystroke, at up to 1,200 headers');
  win.document.body.dispatchEvent(new win.Event('htmx:afterSwap', { bubbles: true }));
  ok(win.BulkTypeahead.vocab() !== b1,
     'L2 …and dropped when the grid is re-rendered, or it goes stale');

  // The tree's cache keyed on the FIRST container alone, so a wiring container
  // that mounted after the state one kept serving a state-only vocabulary.
  const wiring = win.document.getElementById('explorer-tree-wiring');
  const wdata = wiring._treeData;
  delete wiring._treeData;
  win.document.getElementById('explorer-tree-state')._treeData =
    { qubits: { q1: { xy: { amplitude: 1 } } } };
  const t1 = win.TreeTypeahead.vocab();
  ok(t1 && t1.sampling_rate === undefined, 'L3 (setup) wiring is not mounted yet');
  wiring._treeData = wdata;
  const t2 = win.TreeTypeahead.vocab();
  ok(t2 && t2.sampling_rate > 0,
     'L4 a container mounting LATER is read — the cache key was the first '
     + 'model alone, so the wiring keys never appeared: ' + Object.keys(t2 || {}));

  if (fails === 0) console.log('all checks passed (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})().catch(function (e) {
  console.error('harness error: ' + (e && e.stack || e));
  process.exit(1);
});
