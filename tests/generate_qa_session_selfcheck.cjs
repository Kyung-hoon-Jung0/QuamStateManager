// Behavioral pins for the Generate-Config wizard's session findings from the
// 2026-09 real-browser QA pass on a customer chip copy (package gen-session).
// Executes the real _generate.html + generate.js under jsdom.
//
//   F2   the scripts folder keeps FOLLOWING the output folder across a reload /
//        re-mount unless the user typed in the box (the draft carries the
//        touched flag; a legacy draft follows when its path is the derived one)
//   F1   a full-page unload (pagehide: F5 / tab close) saves the draft, so a
//        reload keeps the edits made on the CURRENT step (network, qubit count,
//        a typed-but-uncommitted populate cell)
//   F3   a refusal produced by a user press brings #gen-message into view
//        (header Next, inline rename, manual Auto-allocate); a failed
//        allocation names its reason AT the button; automatic messages never
//        scroll the page
//   F3b  a refused Generate replaces the previous build's success box with the
//        refusal and never POSTs a build
//   F4   Reset wizard keeps the highlighted env as the real selection, so Next
//        moves on from step 1
//   F5   after Reset (and after hydrateFromSpec) QDAC IP/port/link edits land in
//        the CURRENT spec, not in an orphaned object
//
// Run: node tests/generate_qa_session_selfcheck.cjs   (needs jsdom; exit 2 = skip)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const flush = () => new Promise(r => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 6); i++) await flush(); }

const DRAFT_KEY = 'quam_generate_draft';

// A fresh jsdom world with the wizard mounted in #table-pane. `draft` seeds
// sessionStorage BEFORE init (a reload / re-mount restoring the session).
// routes = [{match, reply}] for fetch; unrouted requests hang forever.
function makeWorld(opts) {
  const o = opts || {};
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {}, attach() {}, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  const reveals = [];
  // jsdom has no scrollIntoView — record who asked to be brought into view.
  win.Element.prototype.scrollIntoView = function () { reveals.push(this.id || this.className); };
  const log = [];
  win.fetch = function (url, fo) {
    const entry = { url: String(url), body: fo && fo.body ? JSON.parse(fo.body) : null };
    log.push(entry);
    for (const r of o.routes || []) {
      if (entry.url.indexOf(r.match) >= 0) {
        const data = typeof r.reply === 'function' ? r.reply(entry) : r.reply;
        return win.Promise.resolve({ json: () => win.Promise.resolve(data) });
      }
    }
    return new win.Promise(function () {});
  };
  if (o.draft != null) win.sessionStorage.setItem(DRAFT_KEY, o.draft);
  new win.Function(GEN_JS).call(win);
  const G = win.QuamGen;
  if (!o.noInit) G.init();
  return { win, G, log, reveals };
}

function $(win, id) { return win.document.getElementById(id); }
function setInput(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
function typeOnly(win, el, value) {   // input fired, commit (change) swallowed
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
}
function click(win, el) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true })); }
function pagehide(win) { win.dispatchEvent(new win.Event('pagehide')); }
function draftOf(win) { return win.sessionStorage.getItem(DRAFT_KEY); }

// chassis 1 (MW + LF FEM) and N qubits, left on step 4.
function toStep4(win, G, n) {
  G.goToStep(3);
  setInput(win, $(win, 'gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [
    { slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, $(win, 'gen-qubit-count'), String(n));
}

(async function main() {

  // ── F2: the scripts folder keeps following across a reload ───────────────
  (function f2Follow() {
    const A = 'D:\\x\\chipA', B = 'D:\\x\\chipB';
    const w1 = makeWorld();
    w1.G.goToStep(7);
    setInput(w1.win, $(w1.win, 'gen-output-path'), A);
    ok($(w1.win, 'gen-scripts-path').value === A + '\\state_gen_scripts',
      'F2: first session — the scripts box follows the output folder');
    w1.G.goToStep(8);                       // a step change saves the draft
    const draft = draftOf(w1.win);
    ok(JSON.parse(draft).scriptsPathTouched === false,
      'F2: the draft records the box as NOT touched');

    // Reload / "Load into app" -> sidebar Generate Config: same session draft.
    const w2 = makeWorld({ draft: draft });
    ok(w2.G.state.scriptsPath === A + '\\state_gen_scripts', 'F2: restored path');
    w2.G.goToStep(7);
    setInput(w2.win, $(w2.win, 'gen-output-path'), B);
    ok($(w2.win, 'gen-scripts-path').value === B + '\\state_gen_scripts',
      'F2: after a reload the scripts box still follows the NEW output folder (got ' +
      $(w2.win, 'gen-scripts-path').value + ')');
    ok(w2.G.state.scriptsPath === B + '\\state_gen_scripts',
      'F2: the build would write chip B\'s scripts under chip B, not over chip A\'s');
  })();

  (function f2TypedStaysTyped() {
    const w1 = makeWorld();
    w1.G.goToStep(7);
    setInput(w1.win, $(w1.win, 'gen-output-path'), 'D:\\x\\chipA');
    typeOnly(w1.win, $(w1.win, 'gen-scripts-path'), 'E:\\mine\\scripts');
    w1.G.goToStep(8);
    const w2 = makeWorld({ draft: draftOf(w1.win) });
    ok(w2.win.localStorage.getItem('quam_gen_scripts_path') === null,
      'F2 harness: worlds do not share localStorage (the draft alone carries the choice)');
    w2.G.goToStep(7);
    setInput(w2.win, $(w2.win, 'gen-output-path'), 'D:\\x\\chipB');
    ok(w2.G.state.scriptsPath === 'E:\\mine\\scripts' &&
       $(w2.win, 'gen-scripts-path').value === 'E:\\mine\\scripts',
      'F2: a path the user TYPED survives a reload and stops the follow');
  })();

  (function f2LegacyDraft() {
    const w1 = makeWorld();
    w1.G.goToStep(7);
    setInput(w1.win, $(w1.win, 'gen-output-path'), 'D:\\x\\chipA');
    w1.G.goToStep(8);
    const legacy = JSON.parse(draftOf(w1.win));
    delete legacy.scriptsPathTouched;       // a draft saved before the fix
    const w2 = makeWorld({ draft: JSON.stringify(legacy) });
    w2.G.goToStep(7);
    setInput(w2.win, $(w2.win, 'gen-output-path'), 'D:\\x\\chipC');
    ok(w2.G.state.scriptsPath === 'D:\\x\\chipC\\state_gen_scripts',
      'F2: a legacy draft whose path IS the derived one keeps following');
    legacy.scriptsPath = 'E:\\custom';
    const w3 = makeWorld({ draft: JSON.stringify(legacy) });
    w3.G.goToStep(7);
    setInput(w3.win, $(w3.win, 'gen-output-path'), 'D:\\x\\chipC');
    ok(w3.G.state.scriptsPath === 'E:\\custom',
      'F2: a legacy draft with a custom path stays the user\'s');
  })();

  // ── F1: F5 keeps the current step's edits ────────────────────────────────
  (function f1Network() {
    const w1 = makeWorld();
    w1.G.goToStep(2);                      // the draft is saved on ENTRY (empty host)
    setInput(w1.win, $(w1.win, 'gen-net-host'), '192.168.0.10');
    setInput(w1.win, $(w1.win, 'gen-net-cluster'), 'lab_cluster');
    pagehide(w1.win);                      // F5
    const w2 = makeWorld({ draft: draftOf(w1.win) });
    ok(w2.G.state.step === 2, 'F1: reload reopens step 2');
    ok($(w2.win, 'gen-net-host').value === '192.168.0.10' &&
       $(w2.win, 'gen-net-cluster').value === 'lab_cluster',
      'F1: host + cluster typed on the current step survive F5 (got "' +
      $(w2.win, 'gen-net-host').value + '|' + $(w2.win, 'gen-net-cluster').value + '")');
  })();

  (function f1Qubits() {
    const w1 = makeWorld();
    toStep4(w1.win, w1.G, 5);
    ok(w1.G.state.spec.qubits.length === 5, 'F1: 5 qubits set on step 4');
    pagehide(w1.win);
    const w2 = makeWorld({ draft: draftOf(w1.win) });
    ok(w2.G.state.step === 4 && w2.G.state.spec.qubits.length === 5,
      'F1: the qubit count set on step 4 survives F5 (got ' +
      w2.G.state.spec.qubits.length + ')');
    ok(String($(w2.win, 'gen-qubit-count').value) === '5', 'F1: count box repainted to 5');
  })();

  (function f1PopulateDirtyCell() {
    const w1 = makeWorld();
    toStep4(w1.win, w1.G, 2);
    w1.G.goToStep(6);
    const rf = w1.win.document.querySelector(
      '.gen-pop-in[data-group="qubit"][data-rid="q1"][data-field="RF_freq"]');
    ok(!!rf, 'F1: populate RF cell rendered');
    if (!rf) return;
    typeOnly(w1.win, rf, '4.7');             // still focused: commit never fired
    pagehide(w1.win);
    const w2 = makeWorld({ draft: draftOf(w1.win) });
    const q1 = ((w2.G.state.spec.populate || {}).qubit || {}).q1 || {};
    ok(w2.G.state.step === 6 && q1.RF_freq != null,
      'F1: a populate value typed on step 6 survives F5 (got ' + JSON.stringify(q1.RF_freq) + ')');
  })();

  // ── F3: a refusal produced by a press is brought into view ──────────────
  (function f3TopNext() {
    const w = makeWorld();
    toStep4(w.win, w.G, 3);
    w.G.state.spec.qubit_pairs = [['q1', 'q1']];
    w.G.state.pairsTouched = true;
    w.reveals.length = 0;
    click(w.win, $(w.win, 'gen-next-top'));
    const msg = $(w.win, 'gen-message');
    ok(w.G.state.step === 4 && !msg.hidden &&
       msg.textContent.indexOf('two different qubits') >= 0,
      'F3: the header Next refuses with the pair guard');
    ok(w.reveals.indexOf('gen-message') >= 0,
      'F3: the header-Next refusal scrolls the message into view (reveals: ' +
      JSON.stringify(w.reveals) + ')');
  })();

  (function f3Rename() {
    const w = makeWorld();
    toStep4(w.win, w.G, 3);
    const boxes = w.win.document.querySelectorAll('.gen-qubit-name-in');
    ok(boxes.length === 3, 'F3: three rename boxes');
    if (boxes.length < 2) return;
    setInput(w.win, boxes[1], w.G.state.spec.qubits[0]);   // q2 -> q1: duplicate
    const msg = $(w.win, 'gen-message');
    ok(!msg.hidden && /Duplicate/i.test(msg.textContent),
      'F3: the duplicate rename is refused with a reason');
    // A reveal scroll would be undone by Tab moving focus to the next box
    // (measured in real Chrome) — the reason answers AT the naming block.
    const note = $(w.win, 'gen-naming-note');
    ok(/Duplicate/i.test(note.textContent) && note.classList.contains('gen-topo-caption-warn'),
      'F3: the rename refusal answers beside the rename boxes (note: "' + note.textContent + '")');
    ok(boxes[1].value === w.G.state.spec.qubits[1], 'F3: the refused box shows its valid name again');
    // A later successful rename puts the normal note back.
    setInput(w.win, w.win.document.querySelectorAll('.gen-qubit-name-in')[2], 'q9');
    const note2 = $(w.win, 'gen-naming-note');
    ok(!/Duplicate/i.test(note2.textContent) && !note2.classList.contains('gen-topo-caption-warn'),
      'F3: a successful rename clears the refusal from the note');
  })();

  (function f3ApplyNames() {
    const w = makeWorld();
    toStep4(w.win, w.G, 3);
    const sel = $(w.win, 'gen-naming-preset');
    sel.value = 'custom';
    sel.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    setInput(w.win, $(w.win, 'gen-naming-prefix'), 'x-');      // illegal names
    click(w.win, $(w.win, 'gen-naming-apply'));
    const note = $(w.win, 'gen-naming-note');
    ok(note.classList.contains('gen-topo-caption-warn') && note.textContent.indexOf('✗') === 0,
      'F3: a refused Apply names answers beside the button (note: "' + note.textContent +
      '", qubits ' + JSON.stringify(w.G.state.spec.qubits) + ')');
  })();

  (function f3ApplyGridUnplaced() {
    // The scheme itself refuses (grid letters need every qubit on the board).
    const w = makeWorld();
    toStep4(w.win, w.G, 3);
    const sel = $(w.win, 'gen-naming-preset');
    sel.value = 'grid';
    sel.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    click(w.win, $(w.win, 'gen-naming-apply'));
    const note = $(w.win, 'gen-naming-note');
    ok(note.classList.contains('gen-topo-caption-warn') && note.textContent.indexOf('✗') === 0 &&
       /unplaced/.test(note.textContent),
      'F3: a refused grid Apply names answers beside the button (note: "' + note.textContent + '")');
  })();

  (function f3RailJump() {
    // A forward rail jump carrying a dangling pair is sent back to step 4.
    const w = makeWorld();
    toStep4(w.win, w.G, 3);
    w.G.state.spec.qubit_pairs = [['q1', 'q9']];
    w.G.state.pairsTouched = true;
    w.reveals.length = 0;
    click(w.win, w.win.document.querySelector('#gen-steps li[data-step="6"]'));
    ok(w.G.state.step === 4 && !$(w.win, 'gen-message').hidden,
      'F3: the rail jump is refused back to step 4 with a reason');
    ok(w.reveals.indexOf('gen-message') >= 0,
      'F3: the rail-jump refusal brings the message into view');
  })();

  await (async function f3EnvSelectFails() {
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [{ name: 'X', python: 'C:/x/python.exe' }] } },
      { match: '/generate/probe', reply: { usable: false, missing: ['quam'] } },
      { match: '/generate/select-env', reply: { ok: false, error: 'not a usable interpreter' } }
    ] });
    await settle();
    w.reveals.length = 0;
    click(w.win, w.win.document.querySelector('#gen-env-list .gen-env-row'));
    await settle();
    ok(/not a usable interpreter/.test($(w.win, 'gen-message').textContent) &&
       w.reveals.indexOf('gen-message') >= 0,
      'F3: a refused env pick brings its reason into view (reveals: ' + JSON.stringify(w.reveals) + ')');
  })();

  await (async function f3Allocate() {
    const WHY = 'qdac.ip_address: required when communication_type is Ethernet';
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/allocate', reply: { ok: false, error: WHY } }
    ] });
    toStep4(w.win, w.G, 3);
    w.G.state.env = 'C:/envs/test/python.exe';
    w.reveals.length = 0;
    w.G.goToStep(5);                          // AUTO attempt on entry
    await settle();
    const status = $(w.win, 'gen-allocate-status');
    ok(status.textContent.indexOf('allocation failed') >= 0 &&
       status.textContent.indexOf(WHY) >= 0,
      'F3: a failed allocation names its reason AT the button (got "' +
      status.textContent + '")');
    ok(w.reveals.length === 0,
      'F3: an AUTOMATIC failure never scrolls the page (reveals: ' +
      JSON.stringify(w.reveals) + ')');
    status.textContent = '';
    click(w.win, $(w.win, 'gen-allocate-btn'));   // manual press
    await settle();
    ok(status.textContent.indexOf(WHY) >= 0, 'F3: the manual failure reason is at the button');
    ok(w.reveals.length === 0,
      'F3: the answer is already beside the button — the page does not jump away from it');
  })();

  // ── F3b: a refused Generate replaces the stale success box ───────────────
  await (async function f3bStaleSuccess() {
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/generate/build', reply: { ok: true } }
    ] });
    w.G.state.env = 'C:/envs/test/python.exe';
    const res = $(w.win, 'gen-build-result');
    res.hidden = false;
    res.className = 'gen-build-result gen-build-ok';
    res.textContent = '✓ Generated 5 qubits, 4 pairs into D:\\x\\build1';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:work\\x');   // relative
    w.G.goToStep(8);                         // via the stepper: no step-7 guard
    w.reveals.length = 0;
    click(w.win, $(w.win, 'gen-next'));       // "Generate"
    await settle();
    ok(!w.log.some(e => e.url.indexOf('/generate/build') >= 0),
      'F3b: a relative output path never POSTs a build');
    ok(res.textContent.indexOf('Generated') < 0,
      'F3b: the previous build\'s success box is gone (got "' + res.textContent + '")');
    ok(!res.hidden && res.textContent.indexOf('absolute path') >= 0 &&
       /gen-build-error/.test(res.className),
      'F3b: the refusal answers in the result slot under Generate');
    ok(w.reveals.indexOf('gen-message') >= 0, 'F3b: the refusal is brought into view');
  })();

  await (async function f3bTopologyRefusal() {
    // The final topology gate leaves step 8 — the old success must not wait there.
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } }
    ] });
    toStep4(w.win, w.G, 3);
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.state.spec.qubit_pairs = [['q1', 'q9']];     // dangling: a topology blocker
    w.G.state.pairsTouched = true;
    const res = $(w.win, 'gen-build-result');
    res.hidden = false;
    res.textContent = '✓ Generated 3 qubits into D:\\x\\build1';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\build2');
    w.G.goToStep(8);
    click(w.win, $(w.win, 'gen-next'));
    await settle();
    ok(w.G.state.step === 4, 'F3b: the topology refusal goes back to step 4');
    ok(res.hidden && res.textContent.indexOf('Generated') < 0,
      'F3b: the previous success is cleared for the return to step 8 (got hidden=' +
      res.hidden + ' "' + res.textContent + '")');
  })();

  // ── F4: Reset keeps the highlighted env as the real selection ────────────
  await (async function f4ResetEnv() {
    const PY = 'C:/envs/KRISS_CZ/python.exe';
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: {
        envs: [{ name: 'base', python: 'C:/envs/base/python.exe' },
               { name: 'KRISS_CZ', python: PY }],
        selected: PY } },
      { match: '/generate/probe', reply: { usable: true, versions: {} } }
    ] });
    await settle();
    const sel = () => w.win.document.querySelector('#gen-env-list .gen-env-row.selected');
    ok(w.G.state.env === PY && sel() && sel().dataset.python === PY,
      'F4: the saved env is selected and highlighted');
    w.G.goToStep(3);
    click(w.win, $(w.win, 'gen-reset'));
    ok(w.G.state.step === 1, 'F4: Reset returns to step 1');
    const row = sel();
    ok(row && w.G.state.env === row.dataset.python,
      'F4: after Reset the highlighted env IS the selection (env ' +
      JSON.stringify(w.G.state.env) + ', highlight ' +
      JSON.stringify(row && row.dataset.python) + ')');
    click(w.win, $(w.win, 'gen-next'));
    ok(w.G.state.step === 2, 'F4: Next moves on from step 1 after Reset (step ' +
      w.G.state.step + ', message "' + $(w.win, 'gen-message').textContent + '")');
  })();

  // ── F5: QDAC instrument edits reach the CURRENT spec ─────────────────────
  (function f5AfterReset() {
    const w = makeWorld();
    click(w.win, $(w.win, 'gen-reset'));
    setInput(w.win, $(w.win, 'gen-qdac-ip'), '192.168.8.50');
    ok(w.G.state.spec.qdac && w.G.state.spec.qdac.ip_address === '192.168.8.50',
      'F5: after Reset the typed QDAC IP lands in the spec (got ' +
      JSON.stringify(w.G.state.spec.qdac && w.G.state.spec.qdac.ip_address) + ')');
    setInput(w.win, $(w.win, 'gen-qdac-port'), '5026');
    ok(w.G.state.spec.qdac.port === 5026, 'F5: after Reset the QDAC port lands in the spec');
    const comm = $(w.win, 'gen-qdac-comm');
    comm.value = 'USB';
    comm.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    ok(w.G.state.spec.qdac.communication_type === 'USB' && comm.value === 'USB',
      'F5: after Reset the link switch sticks (state ' +
      w.G.state.spec.qdac.communication_type + ', select ' + comm.value + ')');
    setInput(w.win, $(w.win, 'gen-qdac-usb'), '3');
    ok(w.G.state.spec.qdac.usb_device === 3, 'F5: after Reset the USB device lands in the spec');
    w.G.goToStep(2);                          // saves the draft
    const d = JSON.parse(draftOf(w.win));
    ok(d.spec.qdac.ip_address === '192.168.8.50', 'F5: the draft carries the IP');
  })();

  (function f5AfterHydrate() {
    const w = makeWorld();
    const spec = {
      network: { host: '1.2.3.4', cluster_name: 'C', port: null },
      instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }] }],
                     opx_plus: [], octaves: [] },
      qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], twpas: [], lines: [],
      pair_gate: 'cz_tunable', populate: {},
      qdac: { communication_type: 'Ethernet', ip_address: '10.0.0.1', port: 5025,
              usb_device: null, lib: '@py', qubits: {} }
    };
    w.G.hydrateFromSpec(spec, { mode: 'regenerate' });
    setInput(w.win, $(w.win, 'gen-qdac-ip'), '10.0.0.2');
    ok(w.G.state.spec.qdac.ip_address === '10.0.0.2',
      'F5: after hydrateFromSpec an edited QDAC IP reaches the rebuilt spec (got ' +
      w.G.state.spec.qdac.ip_address + ')');
    const comm = $(w.win, 'gen-qdac-comm');
    comm.value = 'USB';
    comm.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    ok(w.G.state.spec.qdac.communication_type === 'USB' && comm.value === 'USB',
      'F5: after hydrateFromSpec the link switch sticks');
  })();

  if (fails) {
    console.error('generate_qa_session_selfcheck: ' + fails + ' FAILURES');
    process.exit(1);
  }
  console.log('generate_qa_session_selfcheck: all checks passed (' + asserts + ' assertions)');
})().catch(function (e) {
  console.error('generate_qa_session_selfcheck: crashed — ' + (e && e.stack || e));
  process.exit(1);
});
