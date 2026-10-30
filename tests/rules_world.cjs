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
      return mkRow(pid, st, Object.assign(body.rule && body.rule.numbers ? { numbers: body.rule.numbers } : {}, s.extraRow(pid, st, body)));
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


// ---- a gen-additions + gen-rules world with a Populate pair table ------------------------
const F = (raw, has_default, kind, default_repr) => ({ raw, has_default: !!has_default, kind: kind || 'plain', default_repr });
const ROLES = {
  ok: true, versions: {}, qubit_fields: [],
  pulses: { 'pkg.P': { origin: 'lab', fields: { amplitude: F('float', true, 'plain', '0.1'), length: F('int', true, 'plain', '40') } },
            'pkg.Q': { origin: 'lab', fields: { amplitude: F('float', true, 'plain', '0.1'), length: F('int', true, 'plain', '40') } } },
  macros_pair: {
    'pkg.G': { origin: 'lab', default_name: 'cz_bm', doc: 'CZ both-movers', fields: {},
      cz: [{ field: 'flux_pulse_high', on: 'high', required: true }, { field: 'flux_pulse_low', on: 'low', required: true }] },
    'pkg.M': { origin: 'official', default_name: 'cz_m', doc: 'CZ moving', fields: {},
      cz: [{ field: 'flux_pulse_qubit', on: 'moving', required: true }] } },
  macros_qubit: {}, roots: {},
};
const ptr = (q, n) => '#/qubits/' + q + '/z/operations/' + n;
function pulse(q, n, cls, fields) { return { kind: 'pulse', target: 'qubits.' + q + '.z.operations', name: n, 'class': cls || 'pkg.P', fields: fields || { amplitude: 0.12, length: 40 } }; }
function gateBundle(pid, c, t, name, extra) {
  const h = name + '_high_' + c + '_' + t, l = name + '_low_' + c + '_' + t;
  const rid = extra && extra.rule_id;
  const tag = (a) => rid ? Object.assign(a, { rule_id: rid, copied_value: { amplitude: 0.12, length: 40 } }) : a;
  return [tag(pulse(t, h)), tag(pulse(c, l)),
    Object.assign({ kind: 'macro', target: 'qubit_pairs.' + pid + '.macros', name, 'class': 'pkg.G',
      fields: { flux_pulse_high: ptr(t, h), flux_pulse_low: ptr(c, l) } }, rid ? { rule_id: rid } : {})];
}

function makeAddWorld(o) {
  o = o || {};
  const base = makeWorld(Object.assign({}, o, { additions: true }));
  const w = base.w, spec = base.spec;
  const longs = (spec.qubit_pairs || []).map(p => p[0] + '-' + p[1]);
  const origFetch = w.fetch;
  w.fetch = function (url, opts) {
    const u = String(url);
    if (u.indexOf('/generate/roles') >= 0 && u.indexOf('spec=') >= 0) {
      const pairs = {};
      (spec.qubit_pairs || []).forEach(p => {
        const pidx = p[0] + '-' + p[1].replace(/^q/, '');
        pairs[p[0] + '-' + p[1]] = { kind: 'pair', target: 'qubit_pairs.' + pidx + '.macros', label: pidx, pair: pidx, long: p[0] + '-' + p[1], control: p[0], target_q: p[1], coupler: false,
          builder: ['cz_unipolar'], roles: {} };
      });
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ display: { pairs } }) });
    }
    if (u.indexOf('/generate/roles') >= 0) return Promise.resolve({ status: 200, json: () => Promise.resolve(ROLES) });
    if (u.indexOf('/additions/gate-action') >= 0) {
      const b = JSON.parse(opts.body);
      const c = b.pair.split('-')[0], t = b.pair.split('-')[1], pid = c + '-' + t.replace(/^q/, '');
      w.gateActions = (w.gateActions || []); w.gateActions.push(b);
      const adds = gateBundle(pid, c, t, b.action.gate.name);
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true, owner: { pair: pid, long: b.pair, control: c, target_q: t },
        additions: adds, moving_qubit: null, errors: [] }) });
    }
    return origFetch(url, opts);
  };
  const doc = w.document;
  doc.getElementById('pt').innerHTML = '<table id="gen-pop-tbl-pairs"><thead><tr><th>Pair</th><th>x</th></tr></thead><tbody>' +
    '<tr class="gen-pop-setall"><td>Set all →</td><td></td></tr>' +
    (spec.qubit_pairs || []).map(p => '<tr data-group="pairs" data-rid="' + p[0] + '-' + p[1] + '"><td>' + p[0] + '-' + p[1] + '</td><td></td></tr>').join('') +
    '</tbody></table>';
  w.table = () => doc.getElementById('gen-pop-tbl-pairs');
  w.decorate = async () => { w.GenAdditions.decoratePairTable(w.table(), w.ctx); await settle(); };
  w.chip = (pid, name) => doc.querySelector('.gen-gate-added[data-gate="' + name + '"][data-pair="' + pid + '"]');
  w.more = (pid, name) => doc.querySelector('tr[data-rid="' + pid.replace(/^q(\d+)-(\d+)$/, 'q$1-q$2') + '"] .gen-gate-more[data-gate="' + name + '"]');
  w.menuItems = () => Array.from(doc.querySelectorAll('.gen-rule-menu [role="menuitem"], .gen-rule-menu [role="menuitemcheckbox"]'));
  w.GenAdditions.mount(doc.getElementById('host'), w.ctx);
  return base;
}

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { fails++; console.error('FAIL ' + m); } }
function done(name) {
  console.log((fails ? 'FAILED ' : 'OK ') + name + ': ' + asserts + ' assertions, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
}
module.exports = { makeAddWorld, ROLES, pulse, gateBundle, ptr, makeWorld, mkRow, baseSpec, baseRule, applyOk, pidOf, settle, wait, tick, ok, done, read };
