// The Additions section is never left blank while its display facts load, EXECUTED under jsdom.
//
// Drives the real _generate.html + generate.js + gen-additions.js with a fetch whose
// /generate/roles display answers are released by hand.
//
//  W1  while the display facts load, the host shows a loading line (never an empty host)
//  W2  a display answer that arrives after the spec changed still leaves the host non-empty,
//      and once the answer for the current spec arrives the section renders (lab-modules box)
//  W3  a failed display fetch shows the reason and a Retry; Retry asks again and renders
//  W4  a deferred host (not yet near the view) shows the loading line, and refresh() reaches it
//
// Run: node tests/gen_additions_display_wait_selfcheck.cjs   (needs jsdom)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', p), 'utf8');
const HTML = read('web/templates/_generate.html');
const GEN_JS = read('web/static/generate.js');
const TOPO_JS = read('web/static/topo-graph.js');
const ADD_JS = read('web/static/gen-additions.js');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
const tick = () => new Promise(r => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 8; i++) await tick(); }

const ROLES = { ok: true, versions: {}, qubit_fields: [], pulses: {}, macros_pair: {}, macros_qubit: {}, roots: {} };

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' + HTML +
    '</div><div id="addhost"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.showToast = function () {};
  win.pending = [];   // display requests waiting for an answer: {body, resolve, reject}
  win.fetch = function (url, opts) {
    url = String(url);
    const reply = (b) => win.Promise.resolve({ json: () => win.Promise.resolve(b) });
    const body = (opts && opts.body) ? JSON.parse(opts.body) : null;
    if (/\/generate\/roles/.test(url) && body && body.display_spec) {
      return new win.Promise(function (resolve, reject) {
        win.pending.push({ body: body, resolve: function (b) { resolve({ json: () => win.Promise.resolve(b) }); }, reject: reject });
      });
    }
    if (/\/generate\/roles/.test(url)) return reply(ROLES);
    return new win.Promise(function () {});
  };
  new win.Function(TOPO_JS).call(win);
  new win.Function(GEN_JS).call(win);
  new win.Function(ADD_JS).call(win);
  const G = win.QuamGen;
  G.init();
  G.hydrateFromSpec({
    network: { host: '1.2.3.4', cluster_name: 'C', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }], opx_plus: [], octaves: [] },
    qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], twpas: [], pair_gate: 'cz_tunable',
    lines: [{ element: 'q1', line: 'drive' }, { element: 'q1', line: 'flux' }, { element: 'q2', line: 'flux' }],
    populate: { qubit: {}, pairs: {} },
  }, { mode: 'generate' });
  win.GenAdditions._reset();
  win.pending.length = 0;
  return { win: win, st: G._test.state, host: win.document.getElementById('addhost') };
}
const FACTS = { display: { pairs: {} } };

(async function main() {
  // W1 + W2
  {
    const { win, st, host } = makeWorld();
    const ctx = { spec: () => st.spec, changed: () => {} };
    win.GenAdditions.mount(host, ctx);
    ok(host.childNodes.length > 0, 'W1 host is not empty while the display facts load');
    ok(/Loading/.test(host.textContent), 'W1 host says it is loading: ' + JSON.stringify(host.textContent));
    ok(win.pending.length === 1, 'W1 one display request in flight');
    // the spec changes while the request is in flight (Populate prefills a value)
    st.spec.populate.qubit = { q1: { RF_freq: 5e9 } };
    win.pending.shift().resolve(FACTS);
    await settle();
    ok(host.childNodes.length > 0, 'W2 host is not empty after a stale answer arrives');
    ok(win.pending.length === 1, 'W2 the current spec is asked for again');
    if (win.pending.length) {
      ok(win.pending[0].body.display_spec.populate.qubit.q1, 'W2 the second request carries the changed spec');
      win.pending.shift().resolve(FACTS);
    }
    await settle();
    ok(!!host.querySelector('input.gen-add-modules'), 'W2 the section renders once the current answer arrives');
    ok(!host.querySelector('.gen-add-display-wait'), 'W2 the loading line is gone');
  }
  // W3
  {
    const { win, st, host } = makeWorld();
    const ctx = { spec: () => st.spec, changed: () => {} };
    win.GenAdditions.mount(host, ctx);
    win.pending.shift().reject(new Error('boom'));
    await settle();
    ok(/boom/.test(host.textContent), 'W3 the failure reason is shown: ' + JSON.stringify(host.textContent));
    const retry = host.querySelector('.gen-add-display-retry');
    ok(!!retry, 'W3 a Retry button is offered');
    ok(win.pending.length === 0, 'W3 a failure is not retried on its own');
    if (retry) retry.click();
    ok(win.pending.length === 1, 'W3 Retry asks again');
    if (win.pending.length) win.pending.shift().resolve(FACTS);
    await settle();
    ok(!!host.querySelector('input.gen-add-modules'), 'W3 the section renders after a successful retry');
  }
  // W4
  {
    const { win, st, host } = makeWorld();
    const ctx = { spec: () => st.spec, changed: () => {} };
    ok(typeof win.GenAdditions.defer === 'function', 'W4 GenAdditions.defer exists');
    if (win.GenAdditions.defer) win.GenAdditions.defer(host, ctx);
    ok(/Loading/.test(host.textContent), 'W4 a deferred host says it is loading');
    win.GenAdditions.refresh();
    if (win.pending.length) win.pending.shift().resolve(FACTS);
    await settle();
    ok(!!host.querySelector('input.gen-add-modules'), 'W4 refresh() reaches a deferred host');
  }
  console.log('gen_additions_display_wait: ' + asserts + ' asserts, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error(e); process.exit(1); });
