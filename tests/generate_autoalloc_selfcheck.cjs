// Behavioral check for auto-by-default wiring allocation (docs/134).
//
// Customer report: opening a chip's wiring step (the /instrument "Modify
// wiring…" deep link) showed only an Auto-allocate button + the line list,
// no diagram — and the button itself looked dead (its only answer was a
// message bar scrolled out of view when no env was selected; on machines
// WITH a selected env, the regenerate hydrate nulled state.env right back).
//
// Pinned here, executing the real generate.js under jsdom:
//   A1  entering step 5 with an env + qubits auto-runs /generate/allocate,
//       shows the "Allocating channels…" placeholder, then renders the
//       diagram + "Allocated." status on success
//   A2  the full cold chain: step 5 BEFORE env probing finishes shows the
//       waiting placeholder; the first USABLE env (not the first row) is
//       auto-selected via /generate/select-env; the allocation then fires
//       on its own and the diagram appears — zero clicks end to end
//   A3  a manual press with no env answers AT the button (status text),
//       not only in the far-away message bar
//   A4  a failed AUTO attempt latches (step re-entry does not loop the
//       failing request); the manual button stays live and a manual
//       success clears the latch
//   A5  hydrateFromSpec keeps an already-selected env (the regenerate
//       nulling bug) — and honors o.env when given
//
// Run: node tests/generate_autoalloc_selfcheck.cjs   (needs jsdom)
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
const TOPO_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'topo-graph.js'), 'utf8');

let fails = 0;
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const flush = () => new Promise(r => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 6); i++) await flush(); }

// fetch router: routes = [{match: substr, reply: obj|fn}], log = [{url, body}]
function makeWorld(routes) {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {},
    attach(el) { try { el.type = 'text'; } catch (e) {} },
    format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  const diagramCalls = [];
  win.renderInstrumentWiring = function (id) { diagramCalls.push(id); };
  win.confirm = function () { return true; };
  const log = [];
  win.fetch = function (url, opts) {
    const entry = { url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null };
    log.push(entry);
    for (const r of routes || []) {
      if (entry.url.indexOf(r.match) >= 0) {
        const data = typeof r.reply === 'function' ? r.reply(entry) : r.reply;
        return win.Promise.resolve({ json: () => win.Promise.resolve(data) });
      }
    }
    return new win.Promise(function () {});   // unrouted: hang forever
  };
  new win.Function(TOPO_JS).call(win);
  new win.Function(GEN_JS).call(win);
  return { win, log, diagramCalls };
}

function setInput(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

// Minimal 3-qubit CZ world up to step 4 (chassis 1: MW+LF FEM).
function buildWizard(win) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '3');
  G.state.spec.qubit_pairs = [['q1', 'q2']];
  G.state.pairsTouched = true;
  return G;
}

const GOOD_ALLOC = {
  q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }],
        rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' },
             { con: 1, slot: 1, port: 1, io_type: 'input' }] },
  q2: { xy: [{ con: 1, slot: 1, port: 3, io_type: 'output' }],
        rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' },
             { con: 1, slot: 1, port: 1, io_type: 'input' }] },
  q3: { xy: [{ con: 1, slot: 1, port: 4, io_type: 'output' }],
        rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' },
             { con: 1, slot: 1, port: 1, io_type: 'input' }] }
};

function allocCalls(log) {
  return log.filter(e => e.url.indexOf('/generate/allocate') >= 0);
}
function diagramHost(win) {
  return win.document.getElementById('gen-wiring-diagram');
}
function statusText(win) {
  const s = win.document.getElementById('gen-allocate-status');
  return s ? s.textContent : '';
}

(async function main() {

  // ── A1: step-5 entry auto-runs the allocator ─────────────────────────────
  await (async function autoRunOnEntry() {
    let resolveAlloc;
    const { win, log, diagramCalls } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate',
        reply: () => { resolveAlloc = true; return { ok: true, result: { allocation: GOOD_ALLOC } }; } }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';   // as applySelection would set
    ok(allocCalls(log).length === 0, 'A1: no allocation before step 5');
    G.goToStep(5);
    ok(allocCalls(log).length === 1, 'A1: entering step 5 fired /generate/allocate on its own');
    const body = allocCalls(log)[0].body;
    ok(body && body.spec && (body.spec.qubits || []).length === 3,
      'A1: the request carried the spec (3 qubits)');
    ok(diagramHost(win).textContent.indexOf('Checking wiring') >= 0,
      'A1: in-flight placeholder says "Checking wiring…" (got: ' +
      diagramHost(win).textContent.trim() + ')');
    // docs/135: and it VISIBLY waits — a frozen sentence over a multi-second
    // subprocess reads exactly like the dead button this whole file is about.
    ok(diagramHost(win).querySelectorAll('.sm-dots i').length === 3,
      'A1: the waiting line carries the animated ellipsis');
    const st = win.document.getElementById('gen-allocate-status');
    ok(st && st.textContent.indexOf('Checking wiring') >= 0 &&
       st.querySelectorAll('.sm-dots i').length === 3,
      'A1: the BUTTON says it is working too (got: ' +
      (st ? st.textContent.trim() : 'no status el') + ')');
    await settle();
    ok(!!G.state.allocation && !!G.state.allocation.q1,
      'A1: allocation stored from the auto run');
    ok(diagramCalls.indexOf('gen-wiring-diagram') >= 0,
      'A1: the wiring diagram rendered after the auto run');
    ok(statusText(win) === 'Allocated.', 'A1: status reads "Allocated."');
    // Re-entering the step must NOT re-fire (allocation exists).
    G.goToStep(6); G.goToStep(5);
    ok(allocCalls(log).length === 1, 'A1: step re-entry with an allocation is quiet');
  })();

  // ── A2: cold chain — waiting placeholder → auto env pick → diagram ───────
  await (async function coldChain() {
    const { win, log, diagramCalls } = makeWorld([
      { match: '/generate/envs',
        reply: { envs: [{ name: 'bad', python: 'py-bad', kind: 'conda' },
                        { name: 'good', python: 'py-good', kind: 'conda' }] } },
      { match: '/generate/probe?python=py-bad', reply: { usable: false, missing: ['quam'] } },
      { match: '/generate/probe?python=py-good', reply: { usable: true, versions: {} } },
      { match: '/generate/allocate', reply: { ok: true, result: { allocation: GOOD_ALLOC } } }
    ]);
    const G = buildWizard(win);        // init() started loadEnvs; nothing resolved yet
    G.goToStep(5);
    ok(diagramHost(win).textContent.indexOf('Finding a Python environment') >= 0 &&
       diagramHost(win).textContent.indexOf('selected automatically') >= 0,
      'A2: pre-env placeholder names the phase it is in (got: ' +
      diagramHost(win).textContent.trim() + ')');
    ok(diagramHost(win).querySelectorAll('.sm-dots i').length === 3,
      'A2: the env-waiting line animates too (docs/135)');
    ok(allocCalls(log).length === 0, 'A2: no allocation attempt before an env exists');
    await settle(10);                  // envs → probes → auto-pick → allocate
    // The auto-pick is CLIENT-SIDE only (review [7]): /generate/select-env
    // persists machine-wide + rebinds the open chip's type policy, which a
    // mere page view must never trigger.
    const sel = log.filter(e => e.url.indexOf('/generate/select-env') >= 0);
    ok(sel.length === 0, 'A2: auto-pick never POSTs select-env (client-side only)');
    ok(G.state.env === 'py-good',
      'A2: the first USABLE env was picked (not the first row) — got ' + G.state.env);
    ok(allocCalls(log).length === 1,
      'A2: allocation fired on its own once the env existed');
    ok(allocCalls(log)[0].body && allocCalls(log)[0].body.python === 'py-good',
      'A2: the allocate request carries the env explicitly (no persisted selection)');
    ok(!!G.state.allocation, 'A2: allocation stored — zero clicks end to end');
    ok(diagramCalls.indexOf('gen-wiring-diagram') >= 0, 'A2: diagram rendered');
    // The step-1 radio visibly shows the auto-picked env.
    const selRow = win.document.querySelector('.gen-env-row.selected');
    ok(!!selRow && selRow.dataset.python === 'py-good',
      'A2: step 1 shows the auto-picked env as selected');
  })();

  // ── A15: a user click in flight is never overridden by the auto-pick ─────
  await (async function userClickClaims() {
    let releaseSelect = null;
    let releaseGoodProbe = null;
    const { win, log } = makeWorld([
      { match: '/generate/envs',
        reply: { envs: [{ name: 'slowgood', python: 'py-good', kind: 'conda' },
                        { name: 'user', python: 'py-user', kind: 'conda' }] } },
      // The user's chosen env probes BAD immediately (irrelevant — a click is
      // a claim, whatever the probe verdict).
      { match: '/generate/probe?python=py-user', reply: { usable: false, missing: ['quam'] } }
    ]);
    // Hold py-good's probe AND the select POST so the ordering is ours:
    // rows render → user clicks (POST in flight) → the good probe resolves.
    const origFetch = win.fetch;
    win.fetch = function (url, opts) {
      const u = String(url);
      if (u.indexOf('/generate/select-env') >= 0) {
        log.push({ url: u, body: opts && opts.body ? JSON.parse(opts.body) : null });
        return new win.Promise(function (resolve) {
          releaseSelect = function () {
            resolve({ json: () => win.Promise.resolve({ ok: true }) });
          };
        });
      }
      if (u.indexOf('/generate/probe?python=py-good') >= 0) {
        return new win.Promise(function (resolve) {
          releaseGoodProbe = function () {
            resolve({ json: () => win.Promise.resolve({ usable: true, versions: {} }) });
          };
        });
      }
      return origFetch(url, opts);
    };
    const G = buildWizard(win);
    await settle();                    // envs render; py-good probe held
    ok(G.state.env === null, 'A15: nothing picked while the usable probe is held');
    let userRow = null;
    win.document.querySelectorAll('.gen-env-row').forEach(r => {
      if (r.dataset.python === 'py-user') userRow = r;
    });
    ok(!!userRow, 'A15: env rows rendered');
    if (userRow) userRow.dispatchEvent(new win.Event('click', { bubbles: true }));
    ok(log.some(e => e.url.indexOf('/generate/select-env') >= 0),
      'A15: the click POSTed select-env (now in flight)');
    // The usable probe resolves DURING the click's round-trip.
    if (releaseGoodProbe) releaseGoodProbe();
    await settle();
    ok(G.state.env === null,
      'A15: the auto-pick stood down for the in-flight user claim (got ' +
      G.state.env + ')');
    if (releaseSelect) releaseSelect();
    await settle();
    ok(G.state.env === 'py-user', 'A15: the user\'s click landed after release');
  })();

  // ── A3: manual press with no env answers AT the button ───────────────────
  await (async function deadButtonAnswers() {
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } }
    ]);
    const G = buildWizard(win);
    G.goToStep(5);
    win.document.getElementById('gen-allocate-btn')
      .dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    ok(statusText(win).indexOf('Select an environment') >= 0,
      'A3: the press answers next to the button (got: "' + statusText(win) + '")');
    ok(allocCalls(log).length === 0, 'A3: no request without an env');
    const msg = win.document.getElementById('gen-message');
    ok(msg && !msg.hidden && msg.textContent.indexOf('Select an environment') >= 0,
      'A3: the message bar still explains too');
  })();

  // ── A4: failed auto attempt latches; manual stays live; success re-arms ──
  await (async function failureLatch() {
    let allocReply = { ok: false, error: 'boom' };
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: () => allocReply }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 1, 'A4: auto attempt ran once');
    ok(!G.state.allocation, 'A4: failure stored nothing');
    ok(statusText(win).indexOf('allocation failed') >= 0,
      'A4: failure is visible at the button (got: "' + statusText(win) + '")');
    ok(diagramHost(win).textContent.indexOf('Run Auto-allocate') >= 0,
      'A4: placeholder back to the manual instruction after failure');
    G.goToStep(6); G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 1,
      'A4: re-entry does NOT loop the failing request (latched)');
    // The manual button is alive — and its success clears the latch.
    allocReply = { ok: true, result: { allocation: GOOD_ALLOC } };
    win.document.getElementById('gen-allocate-btn')
      .dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    ok(allocCalls(log).length === 2, 'A4: manual press still fires after the latch');
    ok(!!G.state.allocation, 'A4: manual success stored the allocation');
    // The success must RE-ARM auto mode (review [19]): a later topology edit
    // auto-re-allocates. If the latch survived with its fail-sig cleared, the
    // auto path would be dead for the session — this is the observable pin.
    G.goToStep(4);
    setInput(win, win.document.getElementById('gen-qubit-count'), '4');
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 3,
      'A4: after the manual success, a topology edit auto-re-allocates (re-armed)');
  })();

  // ── A4b: an env change re-arms a latched auto failure (same topology) ────
  await (async function envChangeRearms() {
    let allocReply = { ok: false, error: 'boom' };
    const { win, log } = makeWorld([
      { match: '/generate/envs',
        reply: { envs: [{ name: 'e2', python: 'py-e2', kind: 'conda' }] } },
      { match: '/generate/probe?python=py-e2', reply: { usable: false, missing: ['quam'] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/generate/allocate', reply: () => allocReply }
    ]);
    const G = buildWizard(win);
    await settle();                       // envs render (probe rules e2 out — no auto-pick)
    G.state.env = 'py-e1';
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 1, 'A4b: auto attempt failed once');
    allocReply = { ok: true, result: { allocation: GOOD_ALLOC } };
    // User clicks the e2 row — applySelection at step 5 must re-arm + re-run
    // even though the topology is unchanged.
    const row = win.document.querySelector('.gen-env-row');
    ok(!!row, 'A4b: env row rendered');
    if (row) row.dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    ok(allocCalls(log).length === 2,
      'A4b: switching env re-armed the latch and re-ran the allocator');
    ok(!!G.state.allocation, 'A4b: the retry in the new env succeeded');
  })();

  // ── A4c: fixing the spec re-arms a latched auto failure ──────────────────
  await (async function inputFixRearms() {
    let allocReply = { ok: false, error: 'NotEnoughChannels' };
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: () => allocReply }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 1, 'A4c: auto attempt failed once');
    G.goToStep(6); G.goToStep(5); await settle();
    ok(allocCalls(log).length === 1, 'A4c: same input stays latched');
    allocReply = { ok: true, result: { allocation: GOOD_ALLOC } };
    G.goToStep(4);
    setInput(win, win.document.getElementById('gen-qubit-count'), '2');
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 2,
      'A4c: a changed spec re-arms the failed-auto latch on its own');
  })();

  // ── A10: a response for superseded content is DROPPED (review CRITICAL) ──
  await (async function staleResponseDropped() {
    const pending = [];
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } }
    ]);
    const origFetch = win.fetch;
    win.fetch = function (url, opts) {
      if (String(url).indexOf('/generate/allocate') >= 0) {
        log.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
        return new win.Promise(function (resolve) {
          pending.push(function (data) {
            resolve({ json: () => win.Promise.resolve(data) });
          });
        });
      }
      return origFetch(url, opts);
    };
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    ok(allocCalls(log).length === 1, 'A10: first auto-run in flight');
    // Content swap while the response is in flight: hydrate a DIFFERENT spec.
    const specB = JSON.parse(JSON.stringify(G.state.spec));
    specB.qubits = ['q1', 'q2'];
    specB.qubit_pairs = [['q1', 'q2']];
    specB.lines = [];
    G.hydrateFromSpec(specB, { step: 5 });
    await settle();
    const n = allocCalls(log).length;   // hydrate re-entered step 5 → new run
    // Release the FIRST (stale) response now.
    pending[0]({ ok: true, result: { allocation: { qSTALE: { xy: [] } } } });
    await settle();
    ok(!G.state.allocation || !G.state.allocation.qSTALE,
      'A10: the stale response never adopted into the hydrated wizard');
    if (n > 1) {
      pending[1]({ ok: true, result: { allocation: GOOD_ALLOC } });
      await settle();
      ok(!!G.state.allocation && !!G.state.allocation.q1,
        'A10: the CURRENT run\'s response landed normally');
    }
  })();

  // ── A16: a mid-flight topology edit is NOT certified by the response ─────
  // (review CRITICAL repro (a)): the adopted allocation must carry its
  // REQUEST-time signature, so the next Wiring entry sees it stale and
  // re-allocates — a response-time stamp would certify the old allocation
  // as current forever.
  await (async function midFlightEditStaysStale() {
    const pending = [];
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } }
    ]);
    const origFetch = win.fetch;
    win.fetch = function (url, opts) {
      if (String(url).indexOf('/generate/allocate') >= 0) {
        log.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
        return new win.Promise(function (resolve) {
          pending.push(function (data) {
            resolve({ json: () => win.Promise.resolve(data) });
          });
        });
      }
      return origFetch(url, opts);
    };
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    ok(allocCalls(log).length === 1, 'A16: auto-run in flight for the 3-qubit spec');
    // The user goes back, adds a qubit, and RETURNS to the Wiring step — the
    // 4-qubit spec is fully derived, the old run still in flight (no new run
    // starts while one is in flight).
    G.goToStep(4);
    setInput(win, win.document.getElementById('gen-qubit-count'), '4');
    G.goToStep(5);
    ok(allocCalls(log).length === 1, 'A16: no second run while one is in flight');
    // The stale (3-qubit) response lands now, AFTER the edit fully derived —
    // a response-time signature would exactly match the current spec here.
    pending[0]({ ok: true, result: { allocation: GOOD_ALLOC } });
    await settle();
    ok(!!G.state.allocation, 'A16: the same-run response is adopted (no content swap)');
    // Re-entering the Wiring step must see it STALE and re-allocate.
    G.goToStep(6); G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 2,
      'A16: the next entry re-allocates — the mid-flight edit was not certified');
  })();

  // ── A11: the allocation's request-time signature rides the draft ─────────
  await (async function draftSigRoundTrip() {
    const routes = [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: { ok: true, result: { allocation: GOOD_ALLOC } } }
    ];
    const w1 = makeWorld(routes);
    const G1 = buildWizard(w1.win);
    G1.state.env = 'C:/envs/test/python.exe';
    G1.goToStep(5);
    await settle();
    ok(!!G1.state.allocation, 'A11: session 1 allocated');
    G1.goToStep(6);                    // saveDraft persists allocation + sig
    const draft = w1.win.sessionStorage.getItem('quam_generate_draft');
    ok(!!draft && JSON.parse(draft).allocSig,
      'A11: the draft carries the allocation signature');
    // "Reload": a fresh world seeded with the draft.
    const w2 = makeWorld(routes);
    w2.win.sessionStorage.setItem('quam_generate_draft', draft);
    const G2 = w2.win.QuamGen;
    G2.init();
    await settle();
    ok(!!G2.state.allocation, 'A11: draft restored the allocation');
    G2.goToStep(5); await settle();
    const before = allocCalls(w2.log).length;
    G2.goToStep(4);
    setInput(w2.win, w2.win.document.getElementById('gen-qubit-count'), '4');
    G2.goToStep(5); await settle();
    ok(allocCalls(w2.log).length === before + 1,
      'A11: post-reload topology edit still auto-re-allocates (sig survived the draft)');
  })();

  // ── A12: an explicit architecture switch beats the frozen inventory ──────
  await (async function archSwitchBypasses() {
    const { win } = makeWorld([{ match: '/generate/envs', reply: { envs: [] } }]);
    const G = buildWizard(win);
    // A CR source chip: rr/xy per qubit, cross_resonance per pair, no flux.
    const spec = JSON.parse(JSON.stringify(G.state.spec));
    spec.pair_gate = 'cr';
    spec.qubit_pairs = [['q1', 'q2']];
    spec.lines = [];
    ['q1', 'q2', 'q3'].forEach(q => {
      spec.lines.push({ element: q, line: 'resonator', group: 'feedline1', channel: null });
      spec.lines.push({ element: q, line: 'drive', channel: null });
    });
    spec.lines.push({ element: 'q1-q2', line: 'cross_resonance', channel: null });
    G.hydrateFromSpec(spec, { step: 5 });
    await settle();
    const types = () => {
      const c = {};
      G.state.spec.lines.forEach(l => { c[l.line] = (c[l.line] || 0) + 1; });
      return c;
    };
    ok((types().cross_resonance || 0) === 1 && !types().flux && !types().coupler,
      'A12: CR hydrate keeps the source inventory (got ' + JSON.stringify(types()) + ')');
    // Explicit switch to flux-tunable + coupler: the user ASKED for the new
    // line classes — the inventory must not veto them (review [2]).
    const sel = win.document.getElementById('gen-chip-arch');
    sel.value = 'flux_tunable_coupler';
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    G.goToStep(5); await settle();
    ok((types().coupler || 0) === 1,
      'A12: the switched-to gate derives its coupler line (got ' + JSON.stringify(types()) + ')');
    ok((types().flux || 0) === 3,
      'A12: flux derives for every qubit (source was fixed-frequency — no flux truth to keep)');
    // Switch back: the source truth re-applies (no ratchet).
    sel.value = 'fixed_frequency';
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    G.goToStep(5); await settle();
    ok((types().cross_resonance || 0) === 1 && !types().flux && !types().coupler,
      'A12: switching back re-applies the source inventory (got ' + JSON.stringify(types()) + ')');
  })();

  // ── A13: the QDAC checkbox edits the frozen truth (review [4]) ───────────
  await (async function qdacToggleTeachesInventory() {
    const { win } = makeWorld([{ match: '/generate/envs', reply: { envs: [] } }]);
    const G = buildWizard(win);
    const spec = JSON.parse(JSON.stringify(G.state.spec));
    spec.pair_gate = 'cz_tunable';
    spec.qubit_pairs = [];
    spec.qdac = { communication_type: 'Ethernet', ip_address: '1.2.3.4', port: 5025,
                  usb_device: null, lib: '@py',
                  qubits: { q3: { channel: 13, dc_offset: 0 } } };
    spec.lines = [];
    ['q1', 'q2', 'q3'].forEach(q => {
      spec.lines.push({ element: q, line: 'resonator', group: 'feedline1', channel: null });
      spec.lines.push({ element: q, line: 'drive', channel: null });
    });
    spec.lines.push({ element: 'q1', line: 'flux', channel: null });
    spec.lines.push({ element: 'q2', line: 'flux', channel: null });
    G.hydrateFromSpec(spec, { step: 5 });
    await settle();
    const fluxEls = () => G.state.spec.lines
      .filter(l => l.line === 'flux').map(l => l.element).sort().join(',');
    ok(fluxEls() === 'q1,q2', 'A13: QDAC-biased q3 derives no OPX flux (got ' + fluxEls() + ')');
    // Move q3's source back to LF-FEM — the explicit act must (re)create its
    // z line. docs/136 turned the on/off checkbox into a three-way source
    // picker (LF-FEM / QDAC / bias tee); the inventory contract is unchanged.
    G.goToStep(4);
    let q3sel = null;
    win.document.querySelectorAll('#gen-qdac-list .gen-qdac-row').forEach(r => {
      if (r.getAttribute('data-qubit') === 'q3') q3sel = r.querySelector('.gen-qdac-source');
    });
    ok(!!q3sel && q3sel.value === 'qdac', 'A13: q3 renders as QDAC-sourced');
    if (q3sel) {
      q3sel.value = 'opx';
      q3sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    }
    G.goToStep(5); await settle();
    ok(fluxEls() === 'q1,q2,q3',
      'A13: un-QDAC\'ing q3 creates its OPX flux line despite the source inventory (got ' +
      fluxEls() + ')');
  })();

  // ── A14: a line-less source pair is KNOWN, not wizard-added (review [5]) ─
  await (async function linelessPairNotInvented() {
    const { win } = makeWorld([{ match: '/generate/envs', reply: { envs: [] } }]);
    const G = buildWizard(win);
    const spec = JSON.parse(JSON.stringify(G.state.spec));
    spec.pair_gate = 'cz_tunable';
    spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q3']];
    spec.lines = [];
    ['q1', 'q2', 'q3'].forEach(q => {
      spec.lines.push({ element: q, line: 'resonator', group: 'feedline1', channel: null });
      spec.lines.push({ element: q, line: 'drive', channel: null });
      spec.lines.push({ element: q, line: 'flux', channel: null });
    });
    spec.lines.push({ element: 'q1-q2', line: 'coupler', channel: null });
    // q2-q3 deliberately has NO line — a mixed / fixed-coupler pair.
    G.hydrateFromSpec(spec, { step: 5 });
    await settle();
    const couplers = () => G.state.spec.lines
      .filter(l => l.line === 'coupler').map(l => l.element).sort().join(',');
    ok(couplers() === 'q1-q2',
      'A14: no coupler invented for the line-less source pair (got ' + couplers() + ')');
    // A control/target swap must hit the same inventory entry (sorted key).
    G.state.spec.qubit_pairs = [['q2', 'q1'], ['q3', 'q2']];
    G.goToStep(6); G.goToStep(5); await settle();
    ok(couplers() === 'q2-q1',
      'A14: the swapped pair keeps its coupler; the line-less one still gets none (got ' +
      couplers() + ')');
  })();

  // ── A5: hydrateFromSpec keeps a live env selection ───────────────────────
  await (async function hydrateKeepsEnv() {
    const { win } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: { ok: true, result: { allocation: GOOD_ALLOC } } }
    ]);
    const G = buildWizard(win);
    G.state.env = 'py-live';           // what loadEnvs applied moments earlier
    G.hydrateFromSpec(JSON.parse(JSON.stringify(G.state.spec)), { step: 5 });
    ok(G.state.env === 'py-live',
      'A5: hydrateFromSpec preserved the selected env (was nulled before docs/134)');
    const { win: w2 } = makeWorld([{ match: '/generate/envs', reply: { envs: [] } }]);
    const G2 = buildWizard(w2);
    G2.state.env = 'py-live';
    G2.hydrateFromSpec(JSON.parse(JSON.stringify(G2.state.spec)),
                       { step: 1, env: 'py-explicit' });
    ok(G2.state.env === 'py-explicit', 'A5: an explicit o.env still wins');
  })();

  // ── A6: regenerate keeps the SOURCE chip's optional-line inventory ───────
  await (async function regenInventory() {
    const { win } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } }
    ]);
    const G = buildWizard(win);
    // A source chip that flux-biases ONLY q2 (like the real 20Q chip that
    // flux-biases 9 of 20 — deriving the missing z lines ran the allocator
    // out of DC channels).
    const spec = JSON.parse(JSON.stringify(G.state.spec));
    spec.qubit_pairs = [['q1', 'q2']];
    spec.lines = [];
    ['q1', 'q2', 'q3'].forEach(q => {
      spec.lines.push({ element: q, line: 'resonator', group: 'feedline1',
                        channel: { kind: 'mw_fem', con: 1, slot: 1, in_port: 1, out_port: 1 } });
      spec.lines.push({ element: q, line: 'drive',
                        channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: 2 } });
    });
    spec.lines.push({ element: 'q2', line: 'flux',
                      channel: { kind: 'lf_fem', con: 1, out_slot: 2, out_port: 1 } });
    spec.lines.push({ element: 'q1-q2', line: 'coupler',
                      channel: { kind: 'lf_fem', con: 1, out_slot: 2, out_port: 2 } });
    G.hydrateFromSpec(spec, { step: 5 });
    await settle();
    const flux = () => G.state.spec.lines.filter(l => l.line === 'flux')
        .map(l => l.element).sort();
    ok(G.state.mode === 'regenerate', 'A6: world is in regenerate mode');
    ok(flux().join(',') === 'q2',
      'A6: only the source chip\'s flux line survives the re-derive (got ' +
      flux().join(',') + ')');
    ok(G.state.spec.lines.filter(l => l.line === 'coupler').length === 1,
      'A6: the pair\'s coupler line is kept');
    // A wizard-ADDED qubit is not in the inventory → full derived set.
    G.goToStep(4);
    setInput(win, win.document.getElementById('gen-qubit-count'), '4');
    G.goToStep(5);
    await settle();
    ok(flux().indexOf('q4') >= 0,
      'A6: a wizard-added qubit still derives its flux line (got ' + flux().join(',') + ')');
    ok(flux().indexOf('q1') < 0 && flux().indexOf('q3') < 0,
      'A6: pre-existing no-flux qubits stay flux-free after the count edit');
    // A step-3 chassis round-trip must not ratchet the real lines away.
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }];
    G.goToStep(5); await settle();
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [
      { slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
    G.goToStep(5); await settle();
    ok(flux().indexOf('q2') >= 0,
      'A6: q2\'s flux line survives an LF-FEM remove/re-add round-trip');
  })();

  // ── A8: a step-4 topology edit re-allocates on the next Wiring entry ─────
  await (async function topoEditReallocates() {
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: { ok: true, result: { allocation: GOOD_ALLOC } } }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 1, 'A8: first entry allocated');
    // Add a qubit in step 4 → the old allocation is stale for the new spec.
    G.goToStep(4);
    setInput(win, win.document.getElementById('gen-qubit-count'), '4');
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 2,
      'A8: re-entry after a qubit-count edit re-allocates on its own');
    // …and with nothing changed, the next entry is quiet again.
    G.goToStep(6); G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 2, 'A8: unchanged topology stays quiet');
    // A pure pair FLIP (czAutoOrient remaps allocation keys in place) must
    // NOT trigger a spurious re-run — the signature normalizes pair order.
    G.state.spec.qubit_pairs = [['q2', 'q1']];
    G.goToStep(6); G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 2, 'A8: a pair-order flip alone is not a topology change');
  })();

  // ── A9: all probes failing retires the "selected automatically" promise ──
  await (async function noneUsableHonesty() {
    const { win } = makeWorld([
      { match: '/generate/envs',
        reply: { envs: [{ name: 'bad', python: 'py-bad', kind: 'conda' }] } },
      { match: '/generate/probe?python=py-bad', reply: { usable: false, missing: ['quam'] } }
    ]);
    const G = buildWizard(win);
    G.goToStep(5);
    ok(diagramHost(win).textContent.indexOf('selected automatically') >= 0,
      'A9: while probing, the placeholder still promises the auto-selection');
    await settle(10);
    ok(diagramHost(win).textContent.indexOf('No usable Python environment') >= 0,
      'A9: every probe failing switches it to the honest install line (got: ' +
      diagramHost(win).textContent.trim().slice(0, 60) + ')');
  })();

  // ── A7: regen mode never leaks into a later plain-Generate mount ─────────
  await (async function modeReset() {
    const { win } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } }
    ]);
    const G = buildWizard(win);
    G.hydrateFromSpec(JSON.parse(JSON.stringify(G.state.spec)), { step: 1 });
    ok(G.state.mode === 'regenerate' && G.state.buildEndpoint === '/regenerate/build',
      'A7: hydrate put the wizard in regenerate mode');
    // Simulate the next mount of a plain Generate page (fresh root node).
    delete win.document.getElementById('generate-root')._quamGenInit;
    G.init();
    ok(G.state.mode === 'generate', 'A7: a fresh mount is a plain Generate wizard');
    ok(G.state.buildEndpoint === '/generate/build',
      'A7: build endpoint reset (was posting to /regenerate/build with a stale source)');
    ok(G.state.sourcePath === null && G.state.regenLineInventory === null,
      'A7: stale source path + line inventory cleared');
    // Reset wizard inside a regen session leaves regen mode too.
    G.hydrateFromSpec(JSON.parse(JSON.stringify(G.state.spec)), { step: 1 });
    ok(G.state.mode === 'regenerate', 'A7: re-hydrated for the reset check');
    win.document.getElementById('gen-reset')
      .dispatchEvent(new win.Event('click', { bubbles: true }));
    ok(G.state.mode === 'generate' && G.state.sourcePath === null,
      'A7: Reset wizard drops regen mode + source path');
  })();

  if (fails) {
    console.error('generate_autoalloc_selfcheck: ' + fails + ' FAILURES');
    process.exit(1);
  }
  console.log('generate_autoalloc_selfcheck: all checks passed (' + asserts + ' assertions)');
})().catch(function (e) {
  console.error('generate_autoalloc_selfcheck: crashed — ' + (e && e.stack || e));
  process.exit(1);
});
