// Behavioral check: the build result shows the static core budget of the built
// chip (outcome.core_layout from core/core_layout.review_summary).
//
//   over: the warning line sits under the green verdict; the FEM table is open
//   within limit: a FEM with more than 8 rows folds; the risk note shows on sharing
//   none: no core_layout, no section
//
// Run: node tests/generate_core_layout_selfcheck.cjs   (needs jsdom)
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
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

async function world() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.showToast = function () {};
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  await new Promise(function (r) { win.setTimeout(r, 0); });
  return { T: win.QuamGen._test, doc: win.document };
}

function rows(n, core, place) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ element: 'qubits.q' + i + '.xy', core: core || 'own core: no core or thread set',
               shared: !!core, source: core ? 'core' : '', place: place, cost: '1' });
  }
  return out;
}

const LINE = 'Core layout warning: 10 / 8 cores on con1 FEM 1 (MW-FEM); routes 0 / 10 (estimated)';
const RISK = 'Known risk: on QOP 3.8.1 (another device), with xy and resonator on one core';

(async function main() {
  const w = await world();
  const T = w.T;
  const place = 'con1 / FEM 1 (MW-FEM)';
  const over = {
    findings: [{ kind: 'fem_budget', line: LINE,
                 detail: ['Largest groups: a (1).', 'Fix: tick Share one core per qubit (xy + readout), or move elements off this FEM.'] }],
    table: { shared_groups: 0, fems: [{ place: place, over: true, total: 'Total 10 / 8 cores', rows: rows(22, null, place) }] },
    risk_note: null };
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [], warnings: [] }, core_layout: over }, '/o');
  const res = w.doc.getElementById('gen-build-result');
  const ps = Array.prototype.slice.call(res.children);
  const iWarn = ps.findIndex(function (e) { return e.getAttribute('data-core-layout') === 'warn'; });
  ok(/^✓ Generated 1 qubit/.test(ps[0].textContent), '[over] green verdict text stays');
  ok(iWarn === 1 && ps[1].classList.contains('ui-callout--warn'), '[over] warning callout right under the verdict');
  ok(ps[1] && ps[1].firstChild.textContent === LINE, '[over] exact warning line');
  ok(/Fix: tick Share one core per qubit/.test(ps[1].textContent), '[over] Review fix sentence');
  const tbl = res.querySelector('[data-core-layout="table"] table.ui-table');
  ok(tbl && tbl.querySelectorAll('tr').length === 24, '[over] header + 22 rows + total row');
  ok(tbl && !tbl.closest('details'), '[over] an over FEM is never folded');
  ok(/Total 10 \/ 8 cores/.test(tbl.textContent), '[over] total row carries the limit');
  ok(!res.querySelector('[data-core-layout="risk"]'), '[over] no risk note without sharing');

  const within = {
    findings: [],
    table: { shared_groups: 5, fems: [{ place: place, over: false, total: 'Total 5 / 8 cores', rows: rows(10, 'q1_core', place) }] },
    risk_note: RISK + '...' };
  const t2 = T.coreLayoutTable(within);
  ok(t2.querySelector('details.ui-disclosure table.ui-table'), '[within] more than 8 rows fold');
  ok(/Total 5 \/ 8 cores \(10 elements\)/.test(t2.querySelector('summary').textContent), '[within] summary names the total');
  ok(t2.querySelector('[data-core-layout="risk"]') && t2.querySelector('[data-core-layout="risk"]').textContent.indexOf(RISK) === 0, '[within] risk note shown');
  const small = T.coreLayoutTable({ table: { fems: [{ place: place, over: false, total: 'Total 2 / 8 cores', rows: rows(2, null, place) }] } });
  ok(small && !small.querySelector('details'), '[within] 8 rows or fewer stay open');
  ok(T.coreLayoutTable(null) === null && T.coreLayoutTable({ table: null }) === null, '[none] no section');
  ok(T.coreLayoutWarnings(undefined).length === 0, '[none] no warning lines');
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [], warnings: [] } }, '/o');
  ok(!res.querySelector('[data-core-layout="table"]') && !res.querySelector('[data-core-layout="warn"]'), '[none] result without core_layout');

  if (fails) { console.error(fails + ' failed'); process.exit(1); }
  console.log('generate_core_layout_selfcheck ok (' + asserts + ' assertions)');
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
