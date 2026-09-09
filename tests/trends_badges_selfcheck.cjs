// Customer feedback 2026-09-09 — the Trends section's client half, against the
// REAL chip-status.js under jsdom.
//
//  - _params() sends THREE selections separately: the curated qubit chips as
//    ?metrics=, the 2Q/pair badges as ?paths= (comma-separated), and the search
//    box as ?path=. One ?path= could not carry a badge AND a typed family at
//    once, so a badge press used to evict whatever was in the box.
//  - togglePath() flips a badge and reloads.
//  - suggest() renders one row per FAMILY with its entity count, and the click
//    hands back the family PATH (not the row's visible text, which now carries
//    the count).
//
// Run: node tests/trends_badges_selfcheck.cjs   (needs jsdom)
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

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// The section as the server renders it: two chip rows (curated qubit metrics +
// 2Q/pair badges), the search box, the suggest host.
const SECTION =
  '<div class="topo-trends" id="topo-trends">'
  + '<div class="topo-trends-controls"><span class="topo-trends-chips">'
  + '<button type="button" class="topo-trend-chip active" data-trend-metric="T1">T1</button>'
  + '<button type="button" class="topo-trend-chip" data-trend-metric="T2echo">T2 echo</button>'
  + '</span>'
  + '<span class="topo-trends-any">'
  + '<input type="search" id="topo-trend-path" class="topo-trend-path" value="">'
  + '<div class="topo-trend-suggest" id="topo-trend-suggest" hidden></div>'
  + '</span></div>'
  + '<div class="topo-trends-controls topo-trends-2q"><span class="topo-trends-chips">'
  + '<button type="button" class="topo-trend-chip topo-trend-badge"'
  + ' data-trend-path="qubit_pairs.*.gate_fidelity">2Q gate fidelity</button>'
  + '<button type="button" class="topo-trend-chip topo-trend-badge"'
  + ' data-trend-path="qubit_pairs.*.coupler.interaction_offset">Coupler decouple offset<span class="muted"> · 30</span></button>'
  + '</span></div>'
  + '<div class="topo-trends-grid">'
  + '<div class="topo-trend-box" data-trend-metric="gate_fidelity.averaged" data-trend-kind="qubit">'
  + '<div class="topo-trend-chart" id="topo-trend-0"></div></div>'
  + '<div class="topo-trend-box" data-trend-metric="gate_fidelity.averaged" data-trend-kind="pair">'
  + '<div class="topo-trend-chart" id="topo-trend-1"></div></div>'
  + '</div></div>';

function world(rows) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' + SECTION
    + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  win.urls = [];
  win.htmx = { ajax: function (m, u) { win.urls.push(u); return Promise.resolve(); } };
  // The suggester calls a BARE `fetch`, so the mock has to live in the realm
  // the code runs in (docs/144's harness rule), not on the Node global.
  win.__rows = rows || [];
  win.eval('window.fetched = []; window.fetch = function (u) { window.fetched.push(u);'
         + ' return Promise.resolve({ json: function () { return Promise.resolve(window.__rows); } }); };');
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(win);
  return win;
}

// ── 1. _params(): three selections, three parameters ───────────────────────
let win = world();
let doc = win.document;
const T = win.ChipTrends;

const q = () => { win.urls.length = 0; T.toggle('T2echo'); T.toggle('T2echo'); return win.urls[win.urls.length - 1]; };

let url = q();
ok(/[?&]metrics=T1(&|$)/.test(url), 'only the curated chips ride ?metrics= (' + url + ')');
ok(url.indexOf('paths=') < 0 && url.indexOf('path=') < 0, 'no badge, no box: neither path parameter is sent');

// jsdom's outside-only realm never compiles an inline onclick, so the harness
// presses the badge the way the markup does — and pins that the markup really
// says so, since the two halves are what make the button work.
const press = (p) => { T.togglePath(p); return win.urls[win.urls.length - 1]; };
const TPL = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web',
                                      'templates', '_topo_trends.html'), 'utf8');
ok(TPL.indexOf("ChipTrends.togglePath(this.getAttribute('data-trend-path'))") > 0,
   'the badge markup calls togglePath with its own data-trend-path');
url = press('qubit_pairs.*.gate_fidelity');
ok(/[?&]paths=qubit_pairs\.%2A\.gate_fidelity/.test(url) || /[?&]paths=qubit_pairs\.\*\.gate_fidelity/.test(url),
   'a badge press sends the family on ?paths= (' + url + ')');
ok(/[?&]metrics=T1(&|$)/.test(url), 'and the curated selection travels unchanged beside it');
ok(doc.querySelector('.topo-trend-badge').getAttribute('aria-pressed') === 'true',
   'the pressed badge reports itself pressed');

doc.getElementById('topo-trend-path').value = 'qubits.q1.f_01';
url = q();
ok(url.indexOf('&path=qubits.q1.f_01') > 0 || url.indexOf('&path=qubits.q1.f_01'.replace(/\./g, '.')) > 0,
   'the typed family rides ?path= (' + url + ')');
ok(url.indexOf('paths=') > 0, 'the badge is STILL there — one press cannot evict the other');

// a second badge joins the first, comma-separated
url = press('qubit_pairs.*.coupler.interaction_offset');
ok(/paths=[^&]*%2C/.test(url), 'two badges travel as one comma-separated ?paths= (' + url + ')');

// un-pressing removes it again
url = press('qubit_pairs.*.coupler.interaction_offset');
ok(!/paths=[^&]*%2C/.test(url) && url.indexOf('paths=') > 0, 'un-pressing one leaves the other');
ok(doc.querySelectorAll('.topo-trend-badge')[1].getAttribute('aria-pressed') === 'false',
   'and the badge reports itself un-pressed');

// ── 2. the suggester offers FAMILIES ───────────────────────────────────────
const ROWS = [
  { path: 'qubit_pairs.*.macros.cz.fidelity.InterleavedRB.average_gate_fidelity',
    label: 'macros.cz.fidelity.InterleavedRB.average_gate_fidelity',
    scope: 'qubit_pairs', n: 30, changes: 120 },
  { path: 'qubits.*.gate_fidelity.averaged', label: 'gate_fidelity.averaged',
    scope: 'qubits', n: 20, changes: 40 },
  { path: 'ports.con1.offset', label: 'ports.con1.offset', scope: '', n: 1, changes: 3 },
];
win = world(ROWS);
doc = win.document;
const box = doc.getElementById('topo-trend-suggest');
win.ChipTrends.suggest('interleaved');
ok(win.eval('window.fetched.length') === 0, 'the suggester debounces (nothing fetched yet)');

setTimeout(function () {
  const fetched = win.eval('JSON.stringify(window.fetched)');
  ok(fetched.indexOf('/topology/trends/paths?q=interleaved') > 0,
     'it asks the family endpoint (' + fetched + ')');
  setTimeout(function () {
    const rows = Array.from(box.querySelectorAll('.topo-trend-sug'));
    ok(rows.length === 3 && !box.hidden, 'one row per family (' + rows.length + ')');
    ok(rows[0].textContent.indexOf('macros.cz.fidelity.InterleavedRB') === 0,
       'the row shows the TAIL, not the wildcard path (' + rows[0].textContent + ')');
    ok(rows[0].textContent.indexOf('· 30 pairs') > 0,
       'and says how many entities one click charts (' + rows[0].textContent + ')');
    ok(rows[1].textContent.indexOf('· 20 qubits') > 0,
       'a qubit family says qubits (' + rows[1].textContent + ')');
    ok(rows[2].textContent.indexOf('·') < 0,
       'a single non-entity path gets no count (' + rows[2].textContent + ')');
    ok(rows[0].getAttribute('data-path') === ROWS[0].path,
       'the row carries the family PATH, which is what the click sends');

    ok(rows[0].getAttribute('onclick')
         === "ChipTrends.setPath(this.getAttribute('data-path'))",
       'the row calls setPath with that attribute, never with its own text');
    win.urls.length = 0;
    win.ChipTrends.setPath(rows[0].getAttribute('data-path'));
    ok(doc.getElementById('topo-trend-path').value === ROWS[0].path,
       'clicking it fills the box with the path, never with the visible text ('
       + doc.getElementById('topo-trend-path').value + ')');
    ok(box.hidden, 'and closes the list');
    ok((win.urls[win.urls.length - 1] || '').indexOf('path=') > 0, 'and reloads with it');

    // ── 3. render() picks the box by (metric, KIND) ───────────────────────
    win.__plotlyRender = function () {};
    win.eval('window._plotlyRender = function (host) { window.__lastHost = host.id; };');
    win.ChipTrends.render([{ metric: 'gate_fidelity.averaged', kind: 'pair', unit: '',
                             series: [{ entity: 'q1-q2', points: [['20260101_000000', 1],
                                                                  ['20260102_000000', 2]] }] }]);
    ok(win.eval('window.__lastHost') === 'topo-trend-1',
       'a pair chart lands in the PAIR box, not in the qubit box with the same metric ('
       + win.eval('window.__lastHost') + ')');
    win.ChipTrends.render([{ metric: 'gate_fidelity.averaged', kind: 'qubit', unit: '',
                             series: [{ entity: 'q1', points: [['20260101_000000', 1],
                                                               ['20260102_000000', 2]] }] }]);
    ok(win.eval('window.__lastHost') === 'topo-trend-0', 'and a qubit chart in the qubit box');

    // ── 4. the NEWEST press wins the families cap ─────────────────────────
    // The server keeps the first N of what it receives. Emitted in DOM order,
    // the cap discarded whichever badges sit LAST in the row — the user pressed
    // one, it came back aria-pressed="false", and pressing it again reproduced
    // exactly the same trim forever. Newest-first, the press that just happened
    // is always kept and the OLDEST selection is what the trim note names.
    const w4 = world();
    const row = w4.document.querySelector('.topo-trends-2q .topo-trends-chips');
    ['aa', 'bb', 'cc', 'dd'].forEach(function (t) {
      const b = w4.document.createElement('button');
      b.className = 'topo-trend-chip topo-trend-badge';
      b.setAttribute('data-trend-path', 'qubit_pairs.*.' + t);
      row.appendChild(b);
    });
    const T4 = w4.ChipTrends;
    const sent = () => {
      const u = w4.urls[w4.urls.length - 1] || '';
      const m = /[?&]paths=([^&]*)/.exec(u);
      return m ? decodeURIComponent(m[1]).split(',') : [];
    };
    // pressed in DOM order; the row renders them in that same order
    ['qubit_pairs.*.aa', 'qubit_pairs.*.bb', 'qubit_pairs.*.cc'].forEach(T4.togglePath);
    let order = sent();
    ok(order.join('|') === 'qubit_pairs.*.cc|qubit_pairs.*.bb|qubit_pairs.*.aa',
       'the badges travel NEWEST FIRST, so the server trims the oldest ('
       + order.join(',') + ')');
    ok(order[0] === 'qubit_pairs.*.cc',
       'and the badge pressed LAST is the one that cannot be trimmed');

    T4.togglePath('qubit_pairs.*.bb');           // un-press the middle one
    order = sent();
    ok(order.join('|') === 'qubit_pairs.*.cc|qubit_pairs.*.aa',
       'un-pressing removes it from the order too (' + order.join(',') + ')');
    T4.togglePath('qubit_pairs.*.bb');           // press it again — now newest
    order = sent();
    ok(order[0] === 'qubit_pairs.*.bb',
       're-pressing makes it the newest again (' + order.join(',') + ')');

    // A path the order has never seen (a fresh page whose badge the SERVER
    // rendered active) must still be sent — behind the known ones, never lost.
    const unknown = w4.document.querySelector(
      '.topo-trend-badge[data-trend-path="qubit_pairs.*.dd"]');
    unknown.classList.add('active');
    T4.togglePath('qubit_pairs.*.cc');           // any press to force a reload
    T4.togglePath('qubit_pairs.*.cc');
    order = sent();
    ok(order.indexOf('qubit_pairs.*.dd') === order.length - 1,
       'an unrecorded active badge sorts behind the known ones, never dropped ('
       + order.join(',') + ')');
    ok(order.length === 4, 'and every active badge is still sent (' + order.length + ')');

    // An UN-PRESS leaves the order, so a badge the server later renders active
    // again cannot claim the position it held before the user turned it off.
    // The newest deliberate act on that path was switching it OFF; coming back
    // from the server it is news to this page, and news sorts behind.
    const w5 = world();
    ['ee', 'ff', 'gg'].forEach(function (t) {
      const b = w5.document.createElement('button');
      b.className = 'topo-trend-chip topo-trend-badge';
      b.setAttribute('data-trend-path', 'qubit_pairs.*.' + t);
      w5.document.querySelector('.topo-trends-2q .topo-trends-chips').appendChild(b);
    });
    const T5 = w5.ChipTrends;
    const sent5 = () => {
      const m = /[?&]paths=([^&]*)/.exec(w5.urls[w5.urls.length - 1] || '');
      return m ? decodeURIComponent(m[1]).split(',') : [];
    };
    ['qubit_pairs.*.ee', 'qubit_pairs.*.ff', 'qubit_pairs.*.gg'].forEach(T5.togglePath);
    T5.togglePath('qubit_pairs.*.gg');           // …and turn the newest back off
    const gg = w5.document.querySelector(
      '.topo-trend-badge[data-trend-path="qubit_pairs.*.gg"]');
    gg.classList.add('active');                  // the SERVER renders it active
    T5.toggle('T2echo'); T5.toggle('T2echo');    // a reload that touches no badge
    order = sent5();
    ok(order.join('|')
         === 'qubit_pairs.*.ff|qubit_pairs.*.ee|qubit_pairs.*.gg',
       'an un-pressed path loses its old position, so a server-rendered '
       + 'reactivation sorts behind the presses that outlived it ('
       + order.join(',') + ')');

    // ── 5. a badge the SEARCH BOX is driving can be turned off ────────────
    // The server marks a badge active when its path is in the MERGED set of
    // ?path= (the box) and ?paths= (the badges). togglePath only ever
    // manipulated the badge set, so with the same family in the box the press
    // removed one copy, the box sent the other, and the badge came back lit —
    // a control that cannot be switched off. Reproduced with real DOM clicks
    // in headless Chrome before this pin was written.
    const w6 = world();
    const T6 = w6.ChipTrends;
    const BOXED = 'qubit_pairs.*.coupler.interaction_offset';
    const b6 = w6.document.querySelector(
      '.topo-trend-badge[data-trend-path="' + BOXED + '"]');
    const box6 = w6.document.getElementById('topo-trend-path');
    // the state the server renders when the box holds that family: the box
    // carries the path AND the badge is marked active by the merged set
    box6.value = BOXED;
    b6.classList.add('active');
    b6.setAttribute('aria-pressed', 'true');
    T6.togglePath(BOXED);
    const u6 = w6.urls[w6.urls.length - 1] || '';
    ok(b6.getAttribute('aria-pressed') === 'false',
       'pressing a box-driven badge un-presses it');
    ok(box6.value === '',
       'and CLEARS the box, which was the other thing holding it on ('
       + JSON.stringify(box6.value) + ')');
    ok(u6.indexOf('path=') < 0,
       'so the reload asks for neither copy (' + u6 + ')');

    // Turning it off must not clear a box holding something ELSE.
    const w7 = world();
    const T7 = w7.ChipTrends;
    const box7 = w7.document.getElementById('topo-trend-path');
    box7.value = 'qubits.q1.f_01';
    T7.togglePath('qubit_pairs.*.gate_fidelity');   // on
    T7.togglePath('qubit_pairs.*.gate_fidelity');   // off again
    ok(box7.value === 'qubits.q1.f_01',
       'an unrelated typed family survives a badge being turned off ('
       + box7.value + ')');
    ok((w7.urls[w7.urls.length - 1] || '').indexOf('path=qubits.q1.f_01') > 0,
       'and still travels');

    process.exit(fails ? 1 : 0);
  }, 30);
}, 300);
