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

  // ── A4d (QA F7): a failed RE-allocation for a changed chip drops the old ──
  // allocation (diagram, "✓ Wiring valid"), Next refuses with the reason, and
  // a later success clears the stale error beside "Allocated.".
  await (async function failedReallocDropsStale() {
    let allocReply = { ok: true, result: { allocation: GOOD_ALLOC } };
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: () => allocReply }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    await settle();
    const issues = () => win.document.getElementById('gen-wiring-issues').textContent;
    const msg = win.document.getElementById('gen-message');
    ok(!!G.state.allocation && issues().indexOf('Wiring valid') >= 0,
      'A4d: first allocation drawn + validated (got "' + issues() + '")');
    G.goToStep(4);
    setInput(win, win.document.getElementById('gen-qubit-count'), '6');
    allocReply = { ok: false, error: 'NotEnoughChannelsException: cr line q2-3' };
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 2, 'A4d: the changed chip auto-re-allocated');
    ok(G.state.allocation === null, 'A4d: the failed re-allocation drops the stale allocation');
    ok(diagramHost(win).textContent.indexOf('Run Auto-allocate') >= 0,
      'A4d: the diagram falls back to the placeholder (got "' + diagramHost(win).textContent + '")');
    ok(issues().indexOf('Wiring valid') < 0, 'A4d: no "✓ Wiring valid" for a failed chip');
    ok(statusText(win).indexOf('NotEnoughChannels') >= 0,
      'A4d: the reason sits at the button (got "' + statusText(win) + '")');
    G.tryNext();
    ok(G.state.step === 5, 'A4d: Next refuses while the allocation failed for this chip');
    ok(!msg.hidden && msg.textContent.indexOf('Wiring allocation failed') >= 0 &&
       msg.textContent.indexOf('NotEnoughChannels') >= 0,
      'A4d: Next names the failure (got "' + msg.textContent + '")');
    // a pin typed on the failing chip fails too: its row must not keep saying
    // "re-allocating…" (there is no allocation left to re-render from)
    const di = G.state.spec.lines.findIndex(l => l.element === 'q2' && l.line === 'drive');
    const dp = win.document.querySelector('#gen-wiring-table tr[data-idx="' + di + '"] .gen-wiring-pin');
    dp.value = '1/1/5';
    dp.dispatchEvent(new win.Event('change', { bubbles: true }));
    await settle();
    const dcell = win.document.querySelector('#gen-wiring-table tr[data-idx="' + di + '"] .gen-wiring-alloc');
    ok(allocCalls(log).length === 3 && dcell.textContent === '—',
      'A4d: a failed pin re-allocation leaves no "re-allocating…" behind (got "' + dcell.textContent + '")');
    // a manual success clears the old error beside "Allocated."
    msg.hidden = false; msg.textContent = 'NotEnoughChannelsException: old';
    allocReply = { ok: true, result: { allocation: GOOD_ALLOC } };
    win.document.getElementById('gen-allocate-btn')
      .dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    ok(statusText(win) === 'Allocated.' && msg.hidden,
      'A4d: success clears the stale error (status "' + statusText(win) + '", msg "' + msg.textContent + '")');
    G.tryNext();
    ok(G.state.step === 6, 'A4d: Next proceeds once the allocation succeeded');
    G.goToStep(5);
    await settle();
    // a manual re-press failure on an UNCHANGED chip keeps the good allocation
    allocReply = { ok: false, error: 'transient' };
    win.document.getElementById('gen-allocate-btn')
      .dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    ok(!!G.state.allocation, 'A4d: an unchanged chip keeps its allocation on a transient failure');
  })();

  // ── A4e (QA generate-r2-12): fixing a QDAC channel clash re-arms the latch ─
  // The latch keyed on topoSig, which holds only the QDAC qubit KEYS -- the
  // clashing channel values, validated server-side, were invisible to it, so
  // the fixed chip stayed latched (no retry) and Next kept the old reason.
  await (async function qdacFixRearms() {
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: (e) => {
          const qs = (e.body.spec.qdac || {}).qubits || {};
          const seen = {};
          for (const q of Object.keys(qs).sort()) {
            const ch = qs[q].channel;
            if (seen[ch]) return { ok: false, errors: ['qdac.qubits.' + q + '.channel: ' + ch +
                                                      ' is already used by qubit ' + seen[ch]] };
            seen[ch] = q;
          }
          return { ok: true, result: { allocation: GOOD_ALLOC } };
        } }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.state.spec.qdac = { communication_type: 'Ethernet', ip_address: '1.2.3.4', port: 5025,
                          usb_device: null, lib: '@py',
                          qubits: { q2: { channel: 1, dc_offset: 0 }, q3: { channel: 1, dc_offset: 0 } } };
    const msg = win.document.getElementById('gen-message');
    G.goToStep(5); await settle();
    ok(allocCalls(log).length === 1 && G.state.allocation === null,
      'A4e: the clashing QDAC channels fail the auto attempt');
    // an unchanged input stays latched -- and says why again on re-entry
    G.goToStep(4); G.goToStep(5); await settle();
    ok(allocCalls(log).length === 1, 'A4e: an unchanged input stays latched (no hammering)');
    ok(!msg.hidden && msg.textContent.indexOf('already used by qubit q2') >= 0,
      'A4e: a latched re-entry shows the reason again (got hidden=' + msg.hidden + ' "' + msg.textContent + '")');
    // fix the clash (step 4's QDAC row writes spec.qdac.qubits.q3.channel)
    G.goToStep(4);
    G.state.spec.qdac.qubits.q3.channel = 3;
    G.goToStep(5); await settle();
    ok(allocCalls(log).length === 2, 'A4e: the fixed QDAC channel re-arms the auto retry (calls ' +
      allocCalls(log).length + ')');
    ok(!!G.state.allocation && statusText(win) === 'Allocated.',
      'A4e: ...and it succeeds (status "' + statusText(win) + '")');
    G.tryNext();
    ok(G.state.step === 6, 'A4e: Next is no longer refused with the old reason');
    // the success is stable: re-entry neither re-runs nor re-latches
    G.goToStep(5); await settle();
    ok(allocCalls(log).length === 2, 'A4e: a successful QDAC chip does not re-run on re-entry');
  })();

  // ── A20 (QA F5): a typed pin re-allocates; the table/diagram follow it ──
  await (async function typedPinReallocates() {
    const moved = JSON.parse(JSON.stringify(GOOD_ALLOC));
    moved.q1.xy = [{ con: 1, slot: 1, port: 6, io_type: 'output' }];
    let allocReply = { ok: true, result: { allocation: GOOD_ALLOC } };
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: () => allocReply }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    await settle();
    ok(allocCalls(log).length === 1, 'A20: entry allocation');
    const rowOf = (w, g, el, line) => {
      const i = g.state.spec.lines.findIndex(l => l.element === el && l.line === line);
      return w.document.querySelector('#gen-wiring-table tr[data-idx="' + i + '"]');
    };
    // QA F6: the LO-safe partial readout pre-pin is not a "//8" pin
    const rr1 = rowOf(win, G, 'q1', 'resonator').querySelector('.gen-wiring-pin');
    const rr2 = rowOf(win, G, 'q2', 'resonator').querySelector('.gen-wiring-pin');
    ok(rr1.value === '' && rr1.placeholder === 'auto · out 8 / in 2' &&
       !rr1.closest('tr').classList.contains('pinned'),
      'F6: the partial pre-pin renders blank with its ports as the placeholder (got "' +
      rr1.value + '" / "' + rr1.placeholder + '")');
    ok(rr2.value === '' && rr2.placeholder === "auto · q1's feedline",
      'F6: a later feedline member names the lead whose pin the build uses (got "' + rr2.placeholder + '")');
    rr1.focus(); rr1.dispatchEvent(new win.Event('blur'));
    const rrCh = G.state.spec.lines.find(l => l.element === 'q1' && l.line === 'resonator').channel;
    ok(!!rrCh && rrCh.out_port === 8 &&
       allocCalls(log).length === 1,
      'F6: focusing and leaving the blank box keeps the LO-safe pre-pin (no re-allocation)');
    let tr = rowOf(win, G, 'q1', 'drive');
    ok(tr.querySelector('.gen-wiring-alloc').textContent.indexOf('p2') >= 0, 'A20: row shows p2 first');
    allocReply = { ok: true, result: { allocation: moved } };
    const pin = tr.querySelector('.gen-wiring-pin');
    pin.value = '1/1/6';
    pin.dispatchEvent(new win.Event('change', { bubbles: true }));
    const calls = allocCalls(log);
    ok(calls.length === 2, 'A20: a changed pin re-allocates at once (calls ' + calls.length + ')');
    const sent = calls[calls.length - 1].body.spec.lines.find(l => l.element === 'q1' && l.line === 'drive');
    ok(sent && sent.channel && sent.channel.out_port === 6, 'A20: the request carries the typed pin');
    await settle();
    tr = rowOf(win, G, 'q1', 'drive');
    ok(tr.querySelector('.gen-wiring-alloc').textContent.indexOf('p6') >= 0,
      'A20: the Auto-allocated column follows the pin (got "' + tr.querySelector('.gen-wiring-alloc').textContent + '")');
    ok(G.state.allocation.q1.xy[0].port === 6, 'A20: the allocation the diagram draws follows the pin');
    // an unchanged box (blur) does nothing
    tr.querySelector('.gen-wiring-pin').dispatchEvent(new win.Event('blur'));
    ok(allocCalls(log).length === 2, 'A20: an unchanged pin does not re-allocate');

    // a pin typed while a request is in flight runs once more when it answers
    let release;
    const w2 = makeWorld([{ match: '/generate/envs', reply: { envs: [] } }]);
    const gate = new w2.win.Promise(r => { release = r; });
    const G2 = buildWizard(w2.win);
    G2.state.env = 'C:/envs/test/python.exe';
    let n2 = 0, hold = null;
    const base = w2.win.fetch;
    w2.win.fetch = function (url, opts) {
      if (String(url).indexOf('/generate/allocate') < 0) return base(url, opts);
      n2++;
      const body = JSON.parse(opts.body);
      const reply = { ok: true, result: { allocation: GOOD_ALLOC } };
      if (n2 === 1) return gate.then(() => ({ json: () => w2.win.Promise.resolve(reply) }));
      hold = body;
      return w2.win.Promise.resolve({ json: () => w2.win.Promise.resolve(reply) });
    };
    G2.goToStep(5);
    await settle();
    ok(n2 === 1, 'A20: entry request in flight');
    const i2 = G2.state.spec.lines.findIndex(l => l.element === 'q2' && l.line === 'drive');
    const p2 = w2.win.document.querySelector('#gen-wiring-table tr[data-idx="' + i2 + '"] .gen-wiring-pin');
    p2.value = '1/1/7';
    p2.dispatchEvent(new w2.win.Event('change', { bubbles: true }));
    ok(n2 === 1, 'A20: no second request while one is in flight');
    // the user is mid-way through typing the NEXT pin when the answer lands:
    // the table re-render must not eat the text or the focus
    const i3 = G2.state.spec.lines.findIndex(l => l.element === 'q3' && l.line === 'drive');
    const p3 = w2.win.document.querySelector('#gen-wiring-table tr[data-idx="' + i3 + '"] .gen-wiring-pin');
    p3.focus();
    p3.value = '1/1/';
    release();
    await settle(12);
    ok(n2 === 2 && hold && hold.spec.lines[i2].channel.out_port === 7,
      'A20: the in-flight pin re-runs once with the new pin (calls ' + n2 + ')');
    const ae = w2.win.document.activeElement;
    ok(ae !== p3 && ae.classList.contains('gen-wiring-pin') &&
       ae.closest('tr').dataset.idx === String(i3) && ae.value === '1/1/',
      'A20: a re-render keeps the pin being typed (focus + text "' + (ae && ae.value) + '")');
    ok(!G2.state.spec.lines[i3].channel, 'A20: a half-typed pin is not committed by the re-render');
    ae.value = '1/1/5';
    ae.dispatchEvent(new w2.win.Event('blur'));
    await settle();
    ok(n2 === 3 && G2.state.spec.lines[i3].channel && G2.state.spec.lines[i3].channel.out_port === 5,
      'A20: the restored box commits on blur (calls ' + n2 + ')');

    // Next is refused while the allocation is stale for the current pins
    const w3 = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: { ok: true, result: { allocation: GOOD_ALLOC } } }
    ]);
    const G3 = buildWizard(w3.win);
    G3.state.env = 'C:/envs/test/python.exe';
    G3.goToStep(5);
    await settle();
    G3.state.spec.lines.find(l => l.element === 'q3' && l.line === 'drive').channel =
      { kind: 'mw_fem', con: 1, slot: 1, out_port: 5 };   // a pin the allocation never saw
    G3.tryNext();
    ok(G3.state.step === 5 &&
       w3.win.document.getElementById('gen-message').textContent.indexOf('changed since the last allocation') >= 0,
      'A20: Next refuses a stale allocation');
  })();

  // ── A21 (QA generate-r2-06): an xy-only drag must not freeze the feedlines ──
  await (async function xyDragKeepsMuxLive() {
    const { win } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: { ok: true, result: { allocation: GOOD_ALLOC } } }
    ]);
    const G = buildWizard(win);
    const T = G._test;
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5);
    await settle();
    // drag q1's xy p2 -> the free p6
    T.applyPortEdit({ con: 1, slot: 1, port: 2, io: 'output' }, { con: 1, slot: 1, port: 6 });
    ok(G.state.allocation.q1.xy[0].port === 6, 'A21: the xy drag moved q1');
    G.tryNext();   // a drag edits allocation + pins together: not "stale"
    ok(G.state.step === 6, 'A21: Next after a drag is not refused as a stale allocation (step ' + G.state.step + ')');
    G.goToStep(4);
    const mux = win.document.getElementById('gen-mux-size');
    mux.value = '2';
    mux.dispatchEvent(new win.Event('input', { bubbles: true }));
    const sum = win.document.getElementById('gen-qubit-summary');
    ok(/3 qubits · 2 feedlines: q1–q2 · q3$/.test(sum.textContent),
      'A21: caption shows the mux split (got "' + sum.textContent + '")');
    G.goToStep(5);
    const groups = new Set(G.state.spec.lines.filter(l => l.line === 'resonator').map(l => l.group));
    ok(groups.size === 2, 'A21: after an xy-only drag the mux change regroups (' + [...groups] + ')');
    const q1d = G.state.spec.lines.find(l => l.element === 'q1' && l.line === 'drive');
    ok(q1d.channel && q1d.channel.out_port === 6, 'A21: the xy drag itself is kept');
    await settle();
    // a READOUT drag does keep its grouping — and step 4 then says so
    T.applyQubitReadoutEdit({ element: 'q3', io: 'output' }, { con: 1, slot: 1, port: 8 });
    G.goToStep(4);
    mux.value = '3';
    mux.dispatchEvent(new win.Event('input', { bubbles: true }));
    ok(/kept from the step-5 readout wiring/.test(sum.textContent) &&
       sum.classList.contains('gen-qubit-summary-kept'),
      'A21: a kept readout grouping is named in the caption (got "' + sum.textContent + '")');
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
    // QA regenerate-r2-12: the same round trip THROUGH step 4 -- where the
    // hardware fallback runs -- used to leave the chip fixed-frequency / CR.
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }];
    G.goToStep(4); G.goToStep(5); await settle();
    ok(G.state.chipArch === 'fixed_frequency' && G.state.pairGate === 'cr',
      'A6/r2-12: without an LF-FEM the chip builds as fixed-frequency meanwhile');
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [
      { slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
    G.goToStep(4); G.goToStep(5); await settle();
    ok(G.state.chipArch === 'flux_tunable_coupler' && G.state.pairGate === 'cz_tunable' &&
       G.state.qubitFlux === true,
      'A6/r2-12: re-adding the LF-FEM gives the CZ architecture back (got ' +
      G.state.chipArch + '/' + G.state.pairGate + ')');
    ok(flux().join(',') === 'q2,q4' &&
       G.state.spec.lines.filter(l => l.line === 'coupler').length === 1 &&
       !G.state.spec.lines.some(l => l.line === 'cross_resonance'),
      'A6/r2-12: ...with the source line inventory, not CR lines (flux ' + flux().join(',') + ')');
    // the re-add followed by a rail jump PAST step 4 derives the right chip too
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }];
    G.goToStep(4);
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [
      { slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
    G.goToStep(5); await settle();
    ok(G.state.pairGate === 'cz_tunable' && flux().join(',') === 'q2,q4' &&
       !G.state.spec.lines.some(l => l.line === 'cross_resonance'),
      'A6/r2-12: step 3 -> 5 straight after the re-add builds the CZ lines (got ' +
      G.state.pairGate + ', flux ' + flux().join(',') + ')');
  })();

  // ── A24 (QA regenerate-r2-12): a module round trip is lossless ───────────
  await (async function archHoldRoundTrip() {
    const { win } = makeWorld([{ match: '/generate/envs', reply: { envs: [] } }]);
    const G = buildWizard(win);
    const note = () => win.document.getElementById('gen-chip-arch-note');
    const setFems = (fems) => { G.goToStep(3); G.state.spec.instruments.controllers[0].fems = fems; G.goToStep(4); };
    const MW = { slot: 1, fem: 'mw' }, LF = { slot: 2, fem: 'lf' };
    // CZ through an LF-FEM remove / re-add
    G.goToStep(4);
    ok(G.state.chipArch === 'flux_tunable_coupler', 'A24: starts as the default CZ chip');
    G.state.spec.populate.pairs = { 'q1-q2': { cz_interaction_duration: 4.8e-8 } };
    setFems([MW]);
    ok(G.state.chipArch === 'fixed_frequency' && G.state.heldChipArch === 'flux_tunable_coupler',
      'A24: no LF-FEM -> builds fixed-frequency, CZ held');
    ok(note().classList.contains('gen-arch-held') &&
       /Flux-tunable qubits \+ tunable coupler is on hold: it needs an LF-FEM/.test(note().textContent),
      'A24: the note says what is on hold and why (got "' + note().textContent + '")');
    G.goToStep(6); G.goToStep(4);   // a Populate visit while downgraded
    ok(JSON.stringify(G.state.spec.populate.pairs['q1-q2'] || null) === '{"cz_interaction_duration":4.8e-8}',
      'A24: the CZ pair values survive a Populate visit on hold (got ' +
      JSON.stringify(G.state.spec.populate.pairs) + ')');
    // the hold survives a draft reload
    G.goToStep(4);
    delete win.document.getElementById('generate-root')._quamGenInit;
    G.state.heldChipArch = null;   // a fresh page starts with nothing in memory
    G.init();
    ok(G.state.heldChipArch === 'flux_tunable_coupler', 'A24: the hold survives a draft reload');
    setFems([MW, LF]);
    ok(G.state.chipArch === 'flux_tunable_coupler' && G.state.pairGate === 'cz_tunable' &&
       G.state.qubitFlux === true && G.state.heldChipArch === null,
      'A24: the LF-FEM back -> the CZ architecture is back (got ' + G.state.chipArch + '/' + G.state.pairGate + ')');
    ok(!note().classList.contains('gen-arch-held') && !/on hold/.test(note().textContent),
      'A24: ...and the note stops saying "on hold"');
    // CR through an MW-FEM remove / re-add
    const arch = win.document.getElementById('gen-chip-arch');
    arch.value = 'fixed_frequency';
    arch.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(G.state.pairGate === 'cr' && G.state.heldChipArch === null, 'A24: CR picked');
    setFems([LF]);
    ok(G.state.heldChipArch === 'fixed_frequency' && G.state.pairGate !== 'cr',
      'A24: no MW-FEM -> CR held');
    setFems([MW, LF]);
    ok(G.state.chipArch === 'fixed_frequency' && G.state.pairGate === 'cr' && G.state.qubitFlux === false,
      'A24: the MW-FEM back -> CR is back (got ' + G.state.chipArch + '/' + G.state.pairGate + ')');
    // an explicit pick while on hold replaces the hold
    arch.value = 'flux_tunable_fixed_coupler';
    arch.dispatchEvent(new win.Event('change', { bubbles: true }));
    setFems([MW]);
    ok(G.state.heldChipArch === 'flux_tunable_fixed_coupler' && G.state.chipArch === 'fixed_frequency',
      'A24: the fixed-coupler CZ chip is held on an MW-only rack (got ' + G.state.heldChipArch + ')');
    arch.value = 'fixed_frequency';
    arch.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(G.state.heldChipArch === null, 'A24: an explicit pick clears the hold');
    setFems([MW, LF]);
    ok(G.state.chipArch === 'fixed_frequency', 'A24: ...so adding the LF-FEM changes nothing');
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

  // ── A22 (QA generate-r2-11): a box that is not a pin is flagged, kept ────
  // (a fresh copy: earlier worlds' drags mutate the shared GOOD_ALLOC object)
  const cleanAlloc = () => ({
    q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }],
          rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] },
    q2: { xy: [{ con: 1, slot: 1, port: 3, io_type: 'output' }],
          rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] },
    q3: { xy: [{ con: 1, slot: 1, port: 4, io_type: 'output' }],
          rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] }
  });
  await (async function badPinFlagged() {
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: () => ({ ok: true, result: { allocation: cleanAlloc() } }) }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5); await settle();
    const issues = () => win.document.getElementById('gen-wiring-issues').textContent;
    const di = G.state.spec.lines.findIndex(l => l.element === 'q1' && l.line === 'drive');
    const box = () => win.document.querySelector('#gen-wiring-table tr[data-idx="' + di + '"] .gen-wiring-pin');
    function type(v) {
      const b = box(); b.value = v;
      b.dispatchEvent(new win.Event('change', { bubbles: true }));
    }
    ok(issues().indexOf('Wiring valid') >= 0, 'A22: entry allocation validated (got "' + issues() + '" calls ' + allocCalls(log).length + ')');
    for (const bad of ['1/1/99', 'x9', '1.5/1/1', '1/9/1']) {
      type(bad);
      await settle();
      G.goToStep(4); G.goToStep(5); await settle();   // a full re-render
      ok(box().value === bad && box().classList.contains('gen-wiring-pin-invalid') &&
         box().getAttribute('aria-invalid') === 'true',
        'A22: "' + bad + '" stays in its box, flagged invalid (value "' + box().value + '")');
      ok(G.state.spec.lines[di].channel === null,
        'A22: "' + bad + '" is not stored as a pin (' + JSON.stringify(G.state.spec.lines[di].channel) + ')');
      ok(issues().indexOf('is not a pin') >= 0 && issues().indexOf('Wiring valid') < 0,
        'A22: the issues panel names it, no "✓ Wiring valid" beside it (got "' + issues() + '")');
    }
    type('1/1/5');
    await settle();
    ok(!box().classList.contains('gen-wiring-pin-invalid') &&
       G.state.spec.lines[di].channel && G.state.spec.lines[di].channel.out_port === 5,
      'A22: a real pin clears the flag and pins port 5');
    ok(issues().indexOf('is not a pin') < 0, 'A22: ...and leaves the issues panel');
  })();

  // ── A23 (QA regenerate-r2-13): pins left on a moved module are named ─────
  // and carried in one press (MW-FEM moved slot 1 -> 3 in step 3).
  await (async function stalePinsCarried() {
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: (e) => {
          const onOne = (e.body.spec.lines || []).some(l => l.channel && l.channel.kind === 'mw_fem' && l.channel.slot === 1);
          return onOne ? { ok: false, errors: ['stale pins (server)'] }
                       : { ok: true, result: { allocation: cleanAlloc() } };
        } }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5); await settle();
    G.state.spec.lines.forEach(l => {
      if (l.element === 'q1' && l.line === 'drive') l.channel = { kind: 'mw_fem', con: 1, slot: 1, out_port: 2 };
      if (l.element === 'q2' && l.line === 'drive') l.channel = { kind: 'mw_fem', con: 1, slot: 1, out_port: 3 };
    });
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [{ slot: 3, fem: 'mw' }, { slot: 2, fem: 'lf' }];
    G.goToStep(5); await settle();
    const host = win.document.getElementById('gen-wiring-issues');
    ok(host.textContent.indexOf('2 pinned lines (q1 drive, q2 drive) name con1 slot 1') >= 0 &&
       host.textContent.indexOf('no MW-FEM') >= 0,
      'A23: the issues panel names the pins left on the empty slot (got "' + host.textContent + '")');
    const flagged = win.document.querySelectorAll('#gen-wiring-table .gen-wiring-pin-invalid').length;
    ok(flagged === 2, 'A23: both pin boxes are flagged (got ' + flagged + ')');
    const mv = host.querySelector('.gen-wiring-pin-move');
    ok(!!mv && mv.textContent === 'Move them to con1 slot 3',
      'A23: the one free MW-FEM slot is offered (got "' + (mv && mv.textContent) + '")');
    const before = allocCalls(log).length;
    if (mv) mv.dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    const slots = G.state.spec.lines.filter(l => l.line === 'drive' && l.channel && l.channel.con === 1)
      .map(l => l.element + '@' + l.channel.slot + '/' + l.channel.out_port).join(',');
    ok(slots === 'q1@3/2,q2@3/3', 'A23: the pins moved with the module, ports kept (got ' + slots + ')');
    ok(allocCalls(log).length === before + 1 && !!G.state.allocation,
      'A23: the carry re-allocates at once and succeeds');
    ok(host.textContent.indexOf('pinned line') < 0 &&
       !win.document.querySelector('#gen-wiring-table .gen-wiring-pin-invalid'),
      'A23: nothing is flagged any more');
    // Clear: a second move with TWO free slots offers no guess, only Clear.
    G.goToStep(3);
    G.state.spec.instruments.controllers[0].fems = [{ slot: 4, fem: 'mw' }, { slot: 6, fem: 'mw' }, { slot: 2, fem: 'lf' }];
    G.goToStep(5); await settle();
    ok(!host.querySelector('.gen-wiring-pin-move') && !!host.querySelector('.gen-wiring-pin-clear'),
      'A23: two candidate slots -> no guessed move, only Clear');
    host.querySelector('.gen-wiring-pin-clear').dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    ok(G.state.spec.lines.filter(l => l.line === 'drive').every(l => !l.channel || l.channel.slot !== 3),
      'A23: Clear drops the stale pins so they auto-allocate');
  })();

  // ── A25 (QA regenerate-r2-24): two pins on ONE output are named ─────────
  // q2's drive typed onto q1's drive port used to show nothing but the
  // allocator's "not enough channels ... add a FEM". A feedline is one line
  // (all three resonators on one pinned port are fine); a CR line may share.
  await (async function pinCollisionNamed() {
    const { win, log } = makeWorld([
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: (e) => {
          const outs = {};
          let clash = false;
          (e.body.spec.lines || []).forEach(l => {
            if (l.line === 'drive' && l.channel && l.channel.out_port) {
              const k = l.channel.slot + '/' + l.channel.out_port;
              if (outs[k]) clash = true; outs[k] = 1;
            }
          });
          return clash ? { ok: false, errors: ['q1 drive and q2 drive are both pinned (server)'] }
                       : { ok: true, result: { allocation: cleanAlloc() } };
        } }
    ]);
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5); await settle();
    const issues = () => win.document.getElementById('gen-wiring-issues').textContent;
    const idx = (el, line) => G.state.spec.lines.findIndex(l => l.element === el && l.line === line);
    const box = (el, line) => win.document.querySelector('#gen-wiring-table tr[data-idx="' + idx(el, line) + '"] .gen-wiring-pin');
    function type(el, line, v) {
      const b = box(el, line); b.value = v;
      b.dispatchEvent(new win.Event('change', { bubbles: true }));
    }
    // one feedline, every member pinned to the same port: not a collision
    ['q1', 'q2', 'q3'].forEach(q => type(q, 'resonator', '1/1/1'));
    await settle();
    type('q1', 'drive', '1/1/2');
    await settle();
    ok(!win.document.querySelector('#gen-wiring-table .gen-wiring-pin-invalid') &&
       issues().indexOf('pinned to') < 0,
      'A25: a feedline pinned to one port is one line, nothing flagged (got "' + issues() + '")');
    type('q2', 'drive', '1/1/2');
    await settle();
    ok(issues().indexOf('q1 drive and q2 drive are both pinned to con1 slot 1 output 2') >= 0 &&
       issues().indexOf('Wiring valid') < 0,
      'A25: the issues panel names both lines and the port (got "' + issues() + '")');
    ok(box('q1', 'drive').getAttribute('aria-invalid') === 'true' &&
       box('q2', 'drive').getAttribute('aria-invalid') === 'true' &&
       /both pinned/.test(box('q2', 'drive').title),
      'A25: both pin boxes are flagged, with the reason as their title');
    G.goToStep(4); G.goToStep(5); await settle();
    ok(box('q2', 'drive').classList.contains('gen-wiring-pin-invalid') && issues().indexOf('both pinned') >= 0,
      'A25: the flag survives leaving and re-entering step 5');
    // a drive onto the feedline's output port is a collision too
    type('q2', 'drive', '1/1/1');
    await settle();
    ok(issues().indexOf('q1 resonator and q2 drive are both pinned to con1 slot 1 output 1') >= 0,
      'A25: a drive on the feedline output names the feedline once (got "' + issues() + '")');
    // re-pin to a free port: every flag clears and the allocation comes back
    type('q2', 'drive', '1/1/3');
    await settle();
    ok(!win.document.querySelector('#gen-wiring-table .gen-wiring-pin-invalid') &&
       issues().indexOf('pinned to') < 0 && !!G.state.allocation,
      'A25: a free port clears every flag (got "' + issues() + '")');
    // a CR line on its control's xy port shares it by design
    G.state.spec.lines.push({ element: 'q1-q2', line: 'cross_resonance',
                              channel: { kind: 'mw_fem', con: 1, slot: 1, out_port: 2 } });
    type('q3', 'drive', '1/1/4');   // any commit re-reads the pins (no step re-entry: deriveLines would drop the CR line)
    await settle();
    ok(idx('q1-q2', 'cross_resonance') >= 0 && issues().indexOf('pinned to') < 0,
      'A25: a CR line on the control xy port is not flagged (line kept: ' + (idx('q1-q2', 'cross_resonance') >= 0) + ')');
    ok(allocCalls(log).length > 0, 'A25: the allocator was asked (world is live)');
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
