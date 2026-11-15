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
  const infoBoxes = T.coreLayoutWarnings({ findings: [{ kind: 'assumed_type', severity: 'info', line: 'Core layout note: 14 cores on con1', detail: [] }] });
  ok(infoBoxes.length === 1 && infoBoxes[0].classList.contains('ui-callout--note') && !infoBoxes[0].classList.contains('ui-callout--warn')
     && infoBoxes[0].getAttribute('data-core-layout') === 'info', '[info] an info finding is a note callout');
  T.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [], warnings: [] } }, '/o');
  ok(!res.querySelector('[data-core-layout="table"]') && !res.querySelector('[data-core-layout="warn"]'), '[none] result without core_layout');

  // Shared-cores checkbox: draft saved / loaded with either value, and a draft
  // without the key, through the real save / load / sync / build-spec paths.
  const S = T.state;
  const chk = w.doc.getElementById('gen-pin-cores-chk');
  ok(!!chk, '[draft] checkbox exists');
  function optsOf(spec) { return ((spec.populate || {}).options || {}); }
  [false, true].forEach(function (val) {
    S.spec.populate = S.spec.populate || {};
    S.spec.populate.options = Object.assign({}, S.spec.populate.options, { pin_cores: val });
    T.saveDraft();
    const d = T.loadDraft();
    ok(d && optsOf(d.spec).pin_cores === val, '[draft ' + val + '] saved value ' + val);
    S.spec = {};
    T.applyDraft(d);
    ok(optsOf(T.specWithCoBands()).pin_cores === val, '[draft ' + val + '] build spec carries ' + val + ' without visiting step 6');
    chk.checked = !val;
    T.syncPinCoresUi();
    ok(chk.checked === val, '[draft ' + val + '] checkbox shows ' + val);
  });
  // the user unticks: the spec and the saved draft follow the box
  chk.checked = false;
  chk.dispatchEvent(new w.doc.defaultView.Event('change'));
  ok(optsOf(S.spec).pin_cores === false && optsOf(T.loadDraft().spec).pin_cores === false, '[draft] untick reaches spec and draft');
  // a draft from before the key existed: the documented default (on)
  const old = T.loadDraft();
  delete old.spec.populate.options.pin_cores;
  S.spec = {};
  T.applyDraft(old);
  ok(optsOf(T.specWithCoBands()).pin_cores === true, '[draft no key] build spec defaults to true');
  chk.checked = false;
  T.syncPinCoresUi();
  ok(chk.checked === true, '[draft no key] checkbox shows the default');

  // [F3] Re-generate: the option is off and locked with its reason; a new chip keeps it free
  const rnote = w.doc.getElementById('gen-pin-cores-regen');
  const mode0 = S.mode;
  S.mode = 'regenerate';
  S.spec.populate.options.pin_cores = true;
  chk.checked = true;
  T.syncPinCoresUi();
  ok(chk.disabled && !chk.checked && optsOf(S.spec).pin_cores === false,
     '[F3 regen] option unticked, locked and off in the spec');
  ok(rnote && !rnote.hidden && /keeps each element's core from the source chip/.test(rnote.textContent),
     '[F3 regen] the reason is shown beside the option');
  S.mode = mode0;
  T.syncPinCoresUi();
  ok(!chk.disabled && rnote.hidden, '[F3 new chip] option free, no reason line');
  // [F3] the result's kept-cores sentence: shown when the server sent it, absent otherwise
  const KEPT = 'Cores are as on the source chip: a Re-generate keeps each element\'s core from the source, so Share one core per qubit was not applied.';
  const kt = T.coreLayoutTable({ kept_note: KEPT, table: { fems: [{ place: place, over: false, total: 'Total 2 / 8 cores', rows: rows(2, null, place) }] } });
  ok(kt.querySelector('[data-core-layout="kept"]') && kt.querySelector('[data-core-layout="kept"]').textContent === KEPT,
     '[F3 result] kept-cores sentence shown');
  ok(!small.querySelector('[data-core-layout="kept"]'), '[F3 result] no sentence without kept_note');
  // [F8] source key column only when some row has a value; cost names its unit
  const heads = function (el) { return Array.prototype.map.call(el.querySelectorAll('th'), function (h) { return h.textContent; }); };
  ok(heads(small).indexOf('Source key') < 0 && small.querySelector('tr').children.length === 4,
     '[F8] no row has a source key: column dropped ' + JSON.stringify(heads(small)));
  ok(small.querySelector('tr:last-child td').colSpan === 4, '[F8] total row spans the shown columns');
  const withS = T.coreLayoutTable({ table: { fems: [{ place: place, over: false, total: 'Total 1 / 8 cores', rows: rows(2, 'q1_core', place) }] } });
  ok(heads(withS).indexOf('Source key') === 2 && withS.querySelectorAll('tr')[1].children.length === 5,
     '[F8] every row has a source key: column shown');
  const mixedRows = rows(1, 'q1_core', place).concat(rows(1, null, place));
  const mixed = T.coreLayoutTable({ table: { fems: [{ place: place, over: false, total: 'Total 2 / 8 cores', rows: mixedRows }] } });
  ok(heads(mixed).indexOf('Source key') === 2 && mixed.querySelectorAll('tr')[2].children.length === 5,
     '[F8] some rows have a source key: column shown, empty cell kept');
  ok(heads(small).indexOf('Cost (cores)') >= 0, '[F8] cost header names its unit');

  if (fails) { console.error(fails + ' failed'); process.exit(1); }
  console.log('generate_core_layout_selfcheck ok (' + asserts + ' assertions)');
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
