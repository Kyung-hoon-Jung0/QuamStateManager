// C1 + C15: auto_new at settle, and the mandatory settle before Review, in the REAL wizard (jsdom).
//  A1 "+ Add pair" and the two select changes cause no rule request that writes (auto_new does not react)
//  A2 leaving the pair step with auto_new ON => exactly ONE new undo entry; the instance is on the FINAL pair
//  A3 the toast is the wireframe sentence
//  A4 Ctrl+Z x1 removes the gate (the pair stays); no step ever leaves an orphan gate
//  A5 reason "draft" (a restored session) writes nothing, even with auto_new ON
//  A6 entering Review sends exactly ONE expand request; the step is reached
//  A7 a failing settle still lets the user go on, and Review says "rules not checked: <server words>"
//  A8 applyDraft strands an in-flight rule answer (the funnel is wired into the wizard)
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', p), 'utf8');
const HTML = read('web/templates/_generate.html');
const SRC = ['web/static/topo-graph.js', 'web/static/gen-additions.js', 'web/static/gen-rules.js', 'web/static/generate.js'].map(read);

const ALLOC = () => ({ q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }], rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] } });

function makeWizard(o) {
  o = o || {};
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="status-bar"></div><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.toasts = [];
  win.showToast = function (m) { win.toasts.push(String(m)); };
  const srv = { expands: [], applies: [], failExpand: null, held: [] };
  win.fetch = function (url, opts) {
    const u = String(url);
    const reply = (status, body) => win.Promise.resolve({ status, json: () => win.Promise.resolve(body) });
    if (u.indexOf('/rules/expand') >= 0) {
      const body = JSON.parse(opts.body);
      srv.expands.push(body);
      if (srv.failExpand) return reply(400, { ok: false, error: srv.failExpand });
      const pairs = body.spec.qubit_pairs.map(p => {
        const pid = T.pidOf(p);
        const has = (body.spec.additions || []).some(a => a.kind === 'macro' && a.target === 'qubit_pairs.' + pid + '.macros');
        return T.mkRow(pid, has ? 'Already has it' : 'Ready', { existing: has });
      });
      return reply(200, { ok: true, pending: false, groups: {}, pairs, rule_sig: 'rs' + srv.expands.length });
    }
    if (u.indexOf('/rules/apply') >= 0) {
      const body = JSON.parse(opts.body);
      srv.applies.push(body);
      return reply(200, T.applyOk(body));
    }
    if (u.indexOf('/generate/allocate') >= 0) return reply(200, { ok: true, result: { allocation: ALLOC() } });
    return new win.Promise(function () {});
  };
  win.document.elementFromPoint = function () { return null; };
  SRC.forEach(src => new win.Function(src).call(win));
  // Execute the shipped app-wide shortcut listener, not a test imitation.
  const app = read('web/static/app.js').replace(/\r\n/g, '\n');
  const start = app.indexOf('document.addEventListener("keydown", function(evt) {\n    if (!((evt.ctrlKey');
  if (start < 0) throw new Error('wizard undo keyboard listener missing');
  const end = app.indexOf('\n});', start) + '\n});'.length;
  win.eval(app.slice(start, end));
  return { win, srv };
}
const setInput = (win, el, v) => { el.value = String(v); el.dispatchEvent(new win.Event('input', { bubbles: true })); el.dispatchEvent(new win.Event('change', { bubbles: true })); };
const typeField = (win, el, v) => { el.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true })); setInput(win, el, v); };

function build(o) {
  const { win, srv } = makeWizard(o);
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '4');
  G.state.spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q3']];
  G.state.pairsTouched = true;
  const rule = Object.assign(T.baseRule(), { id: 'r1', rev: 1, auto_new: !(o && o.autoNew === false), source: null });
  G.state.spec.addition_rules = [rule];
  G.state.spec.additions = [].concat(T.gateBundle('q1-2', 'q1', 'q2', 'cz_bm', { rule_id: 'r1' }), T.gateBundle('q2-3', 'q2', 'q3', 'cz_bm', { rule_id: 'r1' }));
  G.goToStep(4);
  return { win, srv, G };
}
const pairsOf = G => G.state.spec.qubit_pairs.map(p => p[0] + '-' + p[1]).join(',');
const instTargets = G => (G.state.spec.additions || []).filter(a => a.kind === 'macro' && a.rule_id).map(a => a.target.split('.')[1]);
function orphans(G) {
  const have = {};
  G.state.spec.qubit_pairs.forEach(p => { have[T.pidOf(p)] = 1; });
  return instTargets(G).filter(t => !have[t]);
}
const undo = (win, shiftKey = false) => {
  win.document.body.tabIndex = -1; win.document.body.focus();
  const key = new win.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, shiftKey, bubbles: true, cancelable: true });
  win.document.body.dispatchEvent(key);
  return key.defaultPrevented;
};

(async () => {
  // A1..A4
  let { win, srv, G } = build();
  await T.settle();
  const base = srv.applies.length;
  win.document.getElementById('gen-add-pair').click();
  const rowsSel = (cls) => win.document.querySelectorAll('#gen-pair-list ' + cls);
  const last = (cls) => { const r = rowsSel(cls); return r[r.length - 1]; };
  typeField(win, last('.gen-pair-c'), 'q3');
  typeField(win, last('.gen-pair-t'), 'q4');
  await T.settle();
  ok(pairsOf(G) === 'q1-q2,q2-q3,q3-q4', 'A1 the final pair is q3-q4: ' + pairsOf(G));
  ok(srv.applies.length === base, 'A1 nothing was written while the pair was being edited');
  ok(instTargets(G).length === 2, 'A1 no instance yet');
  let ruleUndos = 0;
  const push = win._wizUndo.pushRestore;
  win._wizUndo.pushRestore = function (...args) { ruleUndos++; return push.apply(this, args); };
  G.goToStep(5);
  await T.settle(); await T.settle();
  ok(srv.applies.length === base + 1 && J(srv.applies[srv.applies.length - 1].pairs) === '["q3-4"]', 'A2 one apply, for the FINAL pair only: ' + J(srv.applies.map(a => a.pairs)));
  ok(instTargets(G).indexOf('q3-4') >= 0 && orphans(G).length === 0, 'A2 the instance is on q3-4: ' + J(instTargets(G)));
  ok(ruleUndos === 1, 'A2 settle adds exactly one RuleTxn to the wizard stack');
  ok(win.toasts.indexOf('Pairs q3-4 added earlier · cz_bm added to them too (rule). Ctrl+Z undoes it.') >= 0, 'A3 toast: ' + J(win.toasts));
  ok(undo(win) === true, 'A4 Ctrl+Z consumed');
  ok(win.toasts.some(t => /Undid: auto-added cz_bm to 1 new pair/.test(t)), 'A4 the undo step is named: ' + J(win.toasts));
  ok(instTargets(G).indexOf('q3-4') < 0 && pairsOf(G) === 'q1-q2,q2-q3,q3-q4', 'A4 x1 removes the gate, the pair stays: ' + pairsOf(G) + ' / ' + J(instTargets(G)));
  ok(undo(win) && pairsOf(G) === 'q1-q2,q2-q3' && orphans(G).length === 0,
    'A4 exactly Ctrl+Z x2 removes the pair without orphans: ' + pairsOf(G));
  const afterUndo = J(G.state.spec);
  ok(undo(win, true) && J(G.state.spec) === afterUndo, 'A4 Ctrl+Shift+Z is swallowed; the wizard has no redo');

  // A5: draft writes nothing
  ({ win, srv, G } = build());
  await T.settle();
  G.state.spec.qubit_pairs.push(['q3', 'q4']);
  win.GenRules.specChanged('draft', G.state.spec);
  G.goToStep(5);
  await T.settle(); await T.settle();
  ok(srv.applies.length === 0 && instTargets(G).indexOf('q3-4') < 0, 'A5 reason draft: nothing written');
  // ... and the same edit made by a person is written
  ({ win, srv, G } = build());
  G.state.spec.qubit_pairs.push(['q3', 'q4']);
  win.GenRules.specChanged('pair_edit', G.state.spec);
  G.goToStep(5);
  await T.settle(); await T.settle();
  ok(srv.applies.length === 1, 'A5 control: the same change by a person IS added');

  // A6/A7 settle before Review
  ({ win, srv, G } = build({ autoNew: false }));
  await T.settle();
  G.state.spec.populate = G.state.spec.populate || {};
  G.state.spec.populate.qubit = Object.assign({}, G.state.spec.populate.qubit, { q1: { RF_freq: 5e9 } });
  G.goToStep(7);
  win.document.getElementById('gen-output-path').value = 'C:\\out';
  G.state.scriptsEnabled = false;
  await T.wait(500); await T.settle();
  G.state.spec.populate.qubit.q2 = { RF_freq: 5.5e9 };   // the chip moved just before Next: the cache is stale
  win.GenRules.specChanged('edit', G.state.spec);
  const before = srv.expands.length;
  G.tryNext();
  await T.settle(); await T.settle();
  ok(G.state.step === 8, 'A6 Review reached: step ' + G.state.step);
  ok(srv.expands.length - before === 1, 'A6 exactly one expand on entering Review: ' + (srv.expands.length - before));
  ok(srv.applies.length === 0, 'A6 auto_new off: nothing written');
  // Even a fresh background answer is followed by the mandatory pre-Review check.
  G.goToStep(7);
  const freshBefore = srv.expands.length;
  G.tryNext(); await T.settle(); await T.settle();
  ok(G.state.step === 8 && srv.expands.length - freshBefore === 1,
    'A6 a fresh cache still gets exactly one pre-Review expand');

  ({ win, srv, G } = build({ autoNew: false }));
  await T.settle();
  G.state.spec.populate = Object.assign({}, G.state.spec.populate, { qubit: { q1: { RF_freq: 6e9 } } });
  G.goToStep(7);
  win.document.getElementById('gen-output-path').value = 'C:\\out';
  G.state.scriptsEnabled = false;
  srv.failExpand = 'the rule check could not run';
  G.tryNext();
  await T.settle(); await T.settle();
  ok(G.state.step === 8, 'A7 the user still gets to Review after a failed settle');
  const box = win.document.createElement('div');
  win.GenRules.renderReviewLines(box, G.state.spec);
  ok(/rules not checked: the rule check could not run/.test(box.textContent), 'A7 Review says so: ' + box.textContent);

  // A8: a REAL draft restore (the wizard re-mounts and init() runs applyDraft) strands an in-flight answer
  ({ win, srv, G } = build());
  await T.settle();
  const gate = { held: [] };
  const real = win.fetch;
  win.fetch = function (url, opts) {
    if (String(url).indexOf('/rules/expand') >= 0) return new win.Promise((res) => gate.held.push(() => real(url, opts).then(res)));
    return real(url, opts);
  };
  const ctx = { spec: () => win.QuamGen.state.spec, changed() {}, feedlines: () => ({}) };
  win.GenRules.openApply({ ctx, rule: T.baseRule() });
  await T.settle();
  ok(gate.held.length === 1, 'A8 a request is in flight');
  win.document.getElementById('table-pane').innerHTML = HTML;     // the wizard is swapped back in
  win.document.dispatchEvent(new win.Event('htmx:afterSwap', { bubbles: true }));
  await T.wait(250); await T.settle();
  ok(gate.held.length >= 2, 'A8 the restore made the dialog ask again: ' + gate.held.length);
  gate.held[0]();                                                  // the OLD answer arrives late
  await T.settle();
  ok(win.document.querySelectorAll('.gen-rule-row').length === 0, 'A8 the stranded answer is not drawn');
  gate.held[gate.held.length - 1]();
  await T.settle();
  ok(win.document.querySelectorAll('.gen-rule-row').length > 0, 'A8 the answer for the restored content is');

  // A9/A10: every other content swap strands rule requests too
  ({ win, srv, G } = build());
  await T.settle();
  let seq = win.GenRules._state().seq;
  win._wizUndo.pushRestore('probe', function () {});
  ok(undo(win) === true && win.GenRules._state().seq > seq, 'A10 an undo restore moves the rule runtime on');
  G.goToStep(5);
  await T.settle();
  seq = win.GenRules._state().seq;
  win.document.getElementById('gen-reset-step').click();
  await T.settle();
  ok(win.GenRules._state().seq > seq, 'A9 resetAllocRuntime (Reset this step) moves the rule runtime on');

  // A11: N rules settle with exactly N expands, even with fresh background answers.
  const multi = T.makeWorld({ nq: 3 });
  multi.spec.addition_rules = ['r1', 'r2'].map((id, i) => Object.assign(T.baseRule(), {
    id, gate: { name: 'cz_' + i, macro_class: 'pkg.G' }, auto_new: false }));
  await multi.w.GenRules.refreshStatuses(multi.w.ctx, true);
  const checkedBefore = multi.srv.expands.length;
  const settled = await multi.w.GenRules.settle(multi.w.ctx, 'pre_review');
  ok(settled.ok && multi.srv.expands.length - checkedBefore === 2,
    'A11 settle checks each rule exactly once, independently of the cache');
  // Warming is a reported failure, never a successful mandatory check.
  const pendingFetch = multi.w.fetch;
  multi.w.fetch = (url, opts) => pendingFetch(url, opts).then(res => ({ status: res.status,
    json: () => res.json().then(body => Object.assign(body, { pending: true })) }));
  const warming = await multi.w.GenRules.settle(multi.w.ctx, 'pre_review');
  ok(!warming.ok && /manifest.*warming/.test(warming.error), 'A11 warming is reported in Review');
  T.done('rules_autonew_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
