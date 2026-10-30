'use strict';
// Shared jsdom world for the addition-rules selfchecks: gen-rules.js (+ optionally
// gen-additions.js) with a mock of the rules server contract.
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (n) => fs.readFileSync(path.join(STATIC, n), 'utf8');

const tick = () => new Promise(r => setTimeout(r, 0));
async function settle(n) { for (let i = 0; i < (n || 8); i++) await tick(); }
const wait = (ms) => new Promise(r => setTimeout(r, ms));

function pidOf(p) { return 'q' + String(p[0]).replace(/^q/, '') + '-' + String(p[1]).replace(/^q/, ''); }

function mkRow(pid, status, extra) {
  const c = pid.replace(/^q/, '').split('-');
  const row = {
    pair: pid, status,
    reason: status === 'Ready' ? 'by Populate frequency' : (status === "Can't" ? 'no coupler line on the pair' : (status === 'Needs a look' ? 'pair facts changed' : 'this rule is already on this pair')),
    additions: [
      { kind: 'pulse', target: 'qubits.q' + c[1] + '.z.operations', name: 'cz_bm_high_q' + c[0] + '_q' + c[1], class: 'pkg.P', fields: {} },
      { kind: 'macro', target: 'qubit_pairs.' + pid + '.macros', name: 'cz_bm', class: 'pkg.G', fields: {} }],
    numbers: { 'high.amplitude': 0.12, 'high.length': 40 }, instance_sig: 'sig-' + pid, key: c.join('-'),
    choices: { high: 'q' + c[1] }, edited: false, existing: false, control: 'q' + c[0],
    long: 'q' + c[0] + '-q' + c[1], diff_vs_current: [], errors: [] };
  return Object.assign(row, extra || {});
}

// The mock server: statusOf(pid, spec, body) -> status; hold = defer expand answers.
function installServer(w, opts) {
  opts = opts || {};
  const s = { expands: [], applies: [], held: [], hold: false, statusOf: opts.statusOf || (() => 'Ready'),
    applyResult: opts.applyResult || null, extraRow: opts.extraRow || (() => ({})) };
  function answer(body) {
    const pairs = (body.spec.qubit_pairs || []).map(p => {
      const pid = pidOf(p);
      const st = s.statusOf(pid, body.spec, body);
      return mkRow(pid, st, s.extraRow(pid, st, body));
    });
    const groups = {};
    ['Ready', 'Needs a look', "Can't", 'Already has it'].forEach(g => { groups[g] = pairs.filter(r => r.status === g); });
    return { ok: true, pending: false, groups, pairs,
      rule_sig: 'rs-' + JSON.stringify(body.spec.populate || {}) + JSON.stringify(body.choices || {}) };
  }
  w.fetch = function (url, o) {
    const body = o && o.body ? JSON.parse(o.body) : {};
    const u = String(url);
    let res;
    if (u.indexOf('/rules/expand') >= 0) { s.expands.push(body); res = { status: 200, body: answer(body) }; }
    else if (u.indexOf('/rules/apply') >= 0) {
      s.applies.push(body);
      res = s.applyResult ? s.applyResult(body) : { status: 200, body: applyOk(body) };
    } else res = { status: 200, body: { ok: true } };
    const make = () => ({ status: res.status, json: () => Promise.resolve(res.body) });
    if (!s.hold || u.indexOf('/rules/expand') < 0) return Promise.resolve(make());
    return new w.Promise((resolve, reject) => {
      const item = { resolve: () => resolve(make()) };
      if (o && o.signal && !s.ignoreAbort) o.signal.addEventListener('abort', () => {
        item.aborted = true; const e = new Error('aborted'); e.name = 'AbortError'; reject(e);
      });
      s.held.push(item);
    });
  };
  s.release = () => { const h = s.held.splice(0); h.forEach(i => { if (!i.aborted) i.resolve(); }); };
  return s;
}
function applyOk(body) {
  const spec = JSON.parse(JSON.stringify(body.spec));
  const rule = Object.assign({ id: 'r1', rev: 1 }, body.rule);
  spec.additions = (spec.additions || []).concat(body.pairs.map(p => ({
    kind: 'macro', target: 'qubit_pairs.' + p + '.macros', name: rule.gate.name, class: 'pkg.G', fields: {}, rule_id: 'r1' })));
  spec.addition_rules = [rule];
  return { ok: true, spec, added: body.pairs.slice(), skipped: [], rule };
}

function baseSpec(nq) {
  nq = nq || 4;
  const qubits = []; for (let i = 1; i <= nq; i++) qubits.push('q' + i);
  const pairs = []; for (let i = 1; i < nq; i++) pairs.push(['q' + i, 'q' + (i + 1)]);
  return { qubits, qubit_pairs: pairs, pair_gate: 'cz_tunable',
    lines: qubits.map(q => ({ element: q, line: 'flux' })), populate: { qubit: {} } };
}
function baseRule() {
  return { source: null, gate: { name: 'cz_bm', macro_class: 'pkg.G', as_default: false },
    items: [{ role: 'high', enabled: true, pulse_class: 'pkg.P', fields_struct: {}, on_choice: 'auto' }],
    numbers: { 'high.amplitude': 0.12, 'high.length': 40 }, source_names: null, auto_new: false, excluded: [], reviewed: {} };
}

function makeWorld(o) {
  o = o || {};
  const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div><div id="host"></div><div id="pt"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const w = dom.window;
  w.toasts = [];
  w.showToast = (m, l) => { w.toasts.push({ m: String(m), l: l || 'info' }); };
  w.undo = [];
  w._wizUndo = { pushRestore: (label, fn) => w.undo.push({ label, fn }) };
  const srv = installServer(w, o.server);
  if (o.additions) w.eval(read('gen-additions.js'));
  w.eval(read('gen-rules.js'));
  const spec = o.spec || baseSpec(o.nq);
  w.spec = spec;
  w.ctx = { spec: () => spec, changed: () => { w.changedN = (w.changedN || 0) + 1; }, feedlines: o.feedlines || (() => ({})) };
  w.key = (el, key, extra) => {
    const ev = new w.KeyboardEvent('keydown', Object.assign({ key, bubbles: true, cancelable: true }, extra || {}));
    el.dispatchEvent(ev);
    return ev;
  };
  w.dlg = () => w.document.querySelector('dialog.gen-rule-dlg');
  w.applyBtn = () => w.document.querySelector('.gen-rule-apply');
  w.cancelBtn = () => w.document.querySelector('.gen-rule-cancel');
  w.rows = () => Array.from(w.document.querySelectorAll('.gen-rule-row'));
  return { w, srv, spec };
}

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
function done(name) {
  console.log((fails ? 'FAILED ' : 'OK ') + name + ': ' + asserts + ' assertions, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
}
module.exports = { makeWorld, mkRow, baseSpec, baseRule, applyOk, pidOf, settle, wait, tick, ok, done, read };
