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
//   F6   leaving a Re-generate session with edits (sidebar swap / F5 / Load
//        different…) asks first; an untouched or just-built session never asks;
//        plain Generate never asks
//   F7   Re-generate neither reads nor clears the Generate draft
//   F8   Reset on the Re-generate page re-fills the wizard from the source chip
//        (the page's own script, driven for real) instead of leaving an empty
//        plain Generate under a header that still names the source
//   F10  a header "Generate" press shows its answer (busy on BOTH buttons, the
//        "Generating…" slot and the result brought into view)
//   r2-18 an unticked scripts export is sent as such; a ticked export with no
//        folder is refused; the recipe report names where it really went
//   regenerate-r2-20  Preview config no longer opens the JSON panel over its
//        own result; "View config JSON" opens it on request
//   generate-r2-10    the panel is parked when leaving step 8 and comes back
//        with it (a closed one stays closed; Reset forgets it)
//   F14  Escape closes the panel (not while typing); a gallery waveform is
//        brought into view and drawn with the house plot theme
//   F8   one build at a time: a double press (header, "Generate anyway", or
//        during the select-env round-trip) POSTs ONE build; "1 pair"
//   generate-r2-13    the build outcome rides the draft: a reload shows the
//        result again, or says a started build's answer never arrived; a
//        re-mount while the build runs says it is still running
//   generate-r2-14    a successful "Load into app" retires the finished draft
//        (the next Generate starts a new chip); a failed load and a
//        Re-generate load leave the Generate draft alone
//   generate-r2-18    step 1 Next refuses an env whose probe said "missing"
//        (a failed probe stays fail-open; the click still selects)
//   F21  Review flags a relative output / scripts folder
//   F20  the user's own re-generate is named in the overwrite question with
//        its report one click away; F5 during a re-generate build asks
//   regenerate-r2-35  a source that changed under the wizard is named at
//        Generate (what gets built, per value) with "Generate anyway" and
//        "Reload the wizard from the chip"
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
  // o.regen: the wizard inside the Re-generate page's #regen-surface.
  // o.regenPage: the REAL _regenerate.html markup + bootstrap script.
  let body = '<div id="table-pane">' + HTML + '</div>';
  if (o.regen) {
    body = '<div id="table-pane"><div class="regen" id="regen-surface">' + HTML + '</div></div>';
  }
  if (o.regenPage) body = '<div id="table-pane">' + REGEN_MARKUP + '</div>';
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>' + body + '</body></html>',
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
  if (o.regenPage) {
    win.QuamGen = G;
    win.openFolderBrowser = function () {};
    new win.Function(REGEN_SCRIPT).call(win);
  }
  return { win, G, log, reveals };
}

// The Re-generate page as shipped: _regenerate.html with its Jinja bits filled
// (the loaded chip = LOADED_CHIP, no deep-link step) and _generate.html included.
const REGEN_TPL = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_regenerate.html'), 'utf8');
const REGEN_MARKUP = REGEN_TPL.slice(0, REGEN_TPL.indexOf('<script>'))
  .replace(/\{#[\s\S]*?#\}/g, '')
  .replace("{% include '_generate.html' %}", HTML)
  .replace(/\{\{ active_path or '' \}\}/, '')
  .replace(/\{\{ active_name or [^}]*\}\}/, 'LOADED_CHIP');
const REGEN_SCRIPT = REGEN_TPL.slice(REGEN_TPL.indexOf('<script>') + 8,
                                     REGEN_TPL.lastIndexOf('</script>'))
  .replace(/\{\{ \(regen_step[^}]*\}\}/, 'null');
if (/\{\{|\{%/.test(REGEN_MARKUP + REGEN_SCRIPT)) {
  console.error('harness: unfilled Jinja left in _regenerate.html');
  process.exit(1);
}

// A reconstructed source chip (what /regenerate/reconstruct returns).
function srcSpec(nq) {
  const qs = [];
  for (let i = 1; i <= (nq || 2); i++) qs.push('q' + i);
  return {
    network: { host: '1.2.3.4', cluster_name: 'SRC', port: null },
    instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }] }],
                   opx_plus: [], octaves: [] },
    qubits: qs, qubit_pairs: [['q1', 'q2']], twpas: [], lines: [],
    pair_gate: 'cz_tunable', populate: {},
    qdac: { communication_type: 'Ethernet', ip_address: '10.0.0.1', port: 5025,
            usb_device: null, lib: '@py', qubits: {} }
  };
}
// An htmx sidebar swap of #table-pane, as htmx raises it (on the pane, bubbling).
function swapAway(win) {
  const e = new win.CustomEvent('htmx:beforeSwap', { bubbles: true, cancelable: true,
    detail: { target: win.document.getElementById('table-pane'), shouldSwap: true } });
  win.document.getElementById('table-pane').dispatchEvent(e);
  return e;
}
function unload(win) {
  const e = new win.Event('beforeunload', { cancelable: true });
  win.dispatchEvent(e);
  return e;
}
function editField(win, el, value) {   // a user edit: focus, type, commit
  el.focus();
  setInput(win, el, value);
  el.blur();
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

  // ── F6: leaving a Re-generate session with edits asks first ──────────────
  function regenWorld() {
    const w = makeWorld({ regen: true });
    w.asked = [];
    w.answer = false;
    w.win.confirm = function (m) { w.asked.push(m); return w.answer; };
    w.G.hydrateFromSpec(srcSpec(), { mode: 'regenerate', sourcePath: 'D:\\src\\chipA' });
    return w;
  }

  (function f6Untouched() {
    const w = regenWorld();
    ok(w.G.regenDirty() === false, 'F6: a freshly hydrated regen session is clean');
    w.G.goToStep(4); w.G.goToStep(2);         // looking around is not editing
    const e = swapAway(w.win);
    ok(!e.defaultPrevented && w.asked.length === 0,
      'F6: an untouched regen session leaves without a prompt (asked ' + w.asked.length + ')');
    ok(!unload(w.win).defaultPrevented, 'F6: F5 on an untouched regen session raises no prompt');
  })();

  (function f6FieldEdit() {
    const w = regenWorld();
    w.G.goToStep(2);
    editField(w.win, $(w.win, 'gen-net-host'), '10.9.9.9');
    ok(w.G.regenDirty() === true, 'F6: a committed field edit makes the session dirty');
    const e = swapAway(w.win);
    ok(w.asked.length === 1 && /no draft/i.test(w.asked[0]),
      'F6: a sidebar swap asks before discarding (asked ' + JSON.stringify(w.asked) + ')');
    ok(e.defaultPrevented && e.detail.shouldSwap === false,
      'F6: Cancel vetoes the swap (defaultPrevented ' + e.defaultPrevented +
      ', shouldSwap ' + e.detail.shouldSwap + ')');
    ok(unload(w.win).defaultPrevented, 'F6: F5 raises the browser\'s leave prompt');
    w.answer = true;
    const e2 = swapAway(w.win);
    ok(!e2.defaultPrevented && e2.detail.shouldSwap !== false, 'F6: OK lets the swap through');
  })();

  (function f6TypedNotCommitted() {
    const w = regenWorld();
    w.G.goToStep(2);
    const host = $(w.win, 'gen-net-host');
    host.focus();
    host.value = '10.7.7.7';
    host.dispatchEvent(new w.win.Event('input', { bubbles: true }));   // no change yet
    ok(unload(w.win).defaultPrevented,
      'F6: F5 with a value typed but not committed still prompts');
  })();

  (function f6Topology() {
    const w = regenWorld();
    w.G.state.spec.qubit_pairs[0] = ['q2', 'q1'];    // a CZ auto-flip is no change…
    ok(w.G.regenDirty() === false, 'F6: a pair orientation flip is not a topology change');
    w.G.state.spec.qubits.push('q3');                // …a new qubit is
    ok(w.G.regenDirty() === true, 'F6: a topology change (button-driven) makes it dirty');
  })();

  (function f6PopulateCell() {
    const w = regenWorld();
    w.G.goToStep(6);
    const rf = w.win.document.querySelector(
      '.gen-pop-in[data-group="qubit"][data-rid="q1"][data-field="RF_freq"]');
    ok(!!rf, 'F6: populate RF cell rendered');
    if (!rf) return;
    rf.focus();
    typeOnly(w.win, rf, '4.9');                      // typed, not committed
    rf.blur();
    ok(w.G.regenDirty() === true, 'F6: a typed populate cell makes the session dirty');
    const e = swapAway(w.win);
    ok(e.defaultPrevented && w.asked.length === 1, 'F6: …and leaving it asks');
  })();

  (function f6SetAll() {
    // Set-all / preset Apply / Re-solve LOs write through markPopulateTouched
    // (no field focus involved — a button-driven change).
    const w = regenWorld();
    w.G.goToStep(6);
    const all = Array.prototype.find.call(
      w.win.document.querySelectorAll('.gen-pop-in:not([data-field])'),
      el => el.tagName === 'INPUT');
    ok(!!all, 'F6: a Set-all cell rendered');
    if (!all) return;
    all.value = '5.1';
    all.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    ok(Object.keys(w.G.state.regenTouched || {}).length > 0, 'F6 harness: Set-all touched cells');
    ok(w.G.regenDirty() === true, 'F6: a Set-all makes the session dirty');
  })();

  await (async function f6BuiltIsClean() {
    const w = makeWorld({ regen: true, routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/regenerate/build', reply: { ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [] } } }
    ] });
    w.asked = [];
    w.win.confirm = function (m) { w.asked.push(m); return false; };
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.hydrateFromSpec(srcSpec(), { mode: 'regenerate', sourcePath: 'D:\\src\\chipA' });
    w.G.goToStep(2);
    editField(w.win, $(w.win, 'gen-net-host'), '10.9.9.9');
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\rebuilt');
    w.G.goToStep(8);
    click(w.win, $(w.win, 'gen-next'));
    await settle();
    ok(w.log.some(e => e.url.indexOf('/regenerate/build') >= 0), 'F6: the regen build ran');
    ok(w.G.regenDirty() === false,
      'F6: a finished build holds the edits — leaving afterwards does not warn');
    swapAway(w.win);
    ok(w.asked.length === 0, 'F6: no prompt after a successful build');
  })();

  (function f6OutputFolderOnly() {
    // The output/scripts folders are mirrored in localStorage and come back.
    const w = regenWorld();
    w.G.goToStep(7);
    editField(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\out');
    ok(w.G.regenDirty() === false, 'F6: choosing the output folder alone is not a lossy edit');
  })();

  (function f6GenerateModeNeverAsks() {
    const w = makeWorld();
    const asked = [];
    w.win.confirm = function (m) { asked.push(m); return false; };
    w.G.goToStep(2);
    editField(w.win, $(w.win, 'gen-net-host'), '10.1.2.3');
    const e = swapAway(w.win);
    ok(!e.defaultPrevented && asked.length === 0, 'F6: plain Generate never asks on a swap');
    ok(!unload(w.win).defaultPrevented, 'F6: plain Generate raises no leave prompt');
    ok(JSON.parse(draftOf(w.win)).spec.network.host === '10.1.2.3',
      'F6: plain Generate still saves its draft on the swap');
  })();

  // ── F7: Re-generate neither reads nor clears the Generate draft ──────────
  (function f7DraftSurvives() {
    const w1 = makeWorld();
    w1.G.goToStep(2);
    setInput(w1.win, $(w1.win, 'gen-net-host'), '10.1.2.3');
    setInput(w1.win, $(w1.win, 'gen-net-cluster'), 'MY_DRAFT_CLUSTER');
    swapAway(w1.win);                              // Generate → sidebar: draft saved
    const draft = draftOf(w1.win);
    ok(draft && JSON.parse(draft).spec.network.host === '10.1.2.3', 'F7: the Generate draft is saved');

    // The Re-generate page, same session storage.
    const w2 = makeWorld({ regen: true, draft: draft });
    ok(w2.G.state.spec.network.host !== '10.1.2.3',
      'F7: the Re-generate page does not load the Generate draft into its session');
    pagehide(w2.win);                              // F5 before the hydrate lands
    swapAway(w2.win);                              // …or a sidebar click
    ok(draftOf(w2.win) === draft, 'F7: an un-hydrated regen page never overwrites the draft');
    w2.G.hydrateFromSpec(srcSpec(), { mode: 'regenerate', sourcePath: 'D:\\src\\chipA' });
    ok(draftOf(w2.win) === draft,
      'F7: hydrating the Re-generate wizard leaves the Generate draft alone (got ' +
      JSON.stringify(draftOf(w2.win) && JSON.parse(draftOf(w2.win)).spec.network) + ')');
    w2.win.confirm = function () { return true; };
    swapAway(w2.win);
    click(w2.win, $(w2.win, 'gen-reset'));         // Reset on the regen page
    ok(draftOf(w2.win) === draft, 'F7: a Reset on the Re-generate page keeps the Generate draft');

    // Back to Generate: the draft restores host + cluster.
    const w3 = makeWorld({ draft: draftOf(w2.win) });
    ok($(w3.win, 'gen-net-host').value === '10.1.2.3' &&
       $(w3.win, 'gen-net-cluster').value === 'MY_DRAFT_CLUSTER',
      'F7: returning to Generate restores host + cluster');
  })();

  // ── F8: Reset on the Re-generate page re-fills from the source chip ──────
  await (async function f8ResetRefills() {
    const w = makeWorld({ regenPage: true, routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/regenerate/reconstruct', reply: function (e) {
        const other = e.body && e.body.folder;
        return { ok: true, spec: srcSpec(other ? 3 : 5),
                 source_folder: other || 'D:\\wc\\key123',
                 source_name: other ? 'other_chip' : 'LOADED_CHIP', notes: [], info_notes: [] };
      } }
    ] });
    await settle();
    const recon = () => w.log.filter(e => e.url.indexOf('/regenerate/reconstruct') >= 0);
    ok(recon().length === 1 && w.G.state.mode === 'regenerate' &&
       w.G.state.spec.qubits.length === 5, 'F8 harness: the page hydrated from the loaded chip');
    ok($(w.win, 'regen-meta').textContent.indexOf('5 qubits') === 0, 'F8 harness: header meta');
    click(w.win, $(w.win, 'gen-reset'));
    ok(w.G.state.mode === 'generate' && w.G.state.sourcePath === null,
      'F8: Reset still drops to plain Generate at once (no stale source can post)');
    await settle();
    ok(recon().length === 2 && recon()[1].body.folder === null,
      'F8: Reset re-reads the same source (the loaded chip) — reconstructs: ' +
      JSON.stringify(recon().map(e => e.body)));
    ok(w.G.state.mode === 'regenerate' && w.G.state.sourcePath === 'D:\\wc\\key123' &&
       w.G.state.spec.qubits.length === 5 && w.G.state.buildEndpoint === '/regenerate/build',
      'F8: after Reset the wizard is the source chip again (mode ' + w.G.state.mode +
      ', qubits ' + w.G.state.spec.qubits.length + ')');
    ok($(w.win, 'regen-meta').textContent.indexOf('5 qubits') === 0 &&
       $(w.win, 'regen-chip').textContent === 'LOADED_CHIP',
      'F8: the header names the source and its counts again');
    ok(w.G.state.step === 1, 'F8: Reset starts over at step 1');

    // After "Load different…", a Reset re-reads THAT folder.
    const src = $(w.win, 'regen-src-input');
    src.value = 'D:\\x\\other_chip';
    src.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    await settle();
    ok(w.G.state.spec.qubits.length === 3 && $(w.win, 'regen-chip').textContent === 'other_chip',
      'F8 harness: Load different… hydrated the other chip');
    click(w.win, $(w.win, 'gen-reset'));
    await settle();
    ok(recon().length === 4 && recon()[3].body.folder === 'D:\\x\\other_chip' &&
       w.G.state.sourcePath === 'D:\\x\\other_chip',
      'F8: after Load different…, Reset re-reads that folder (got ' +
      JSON.stringify(recon()[recon().length - 1].body) + ')');

    // F6 on the page: Load different… over a dirty session asks first.
    w.G.goToStep(2);
    editField(w.win, $(w.win, 'gen-net-host'), '10.5.5.5');
    let asked = 0;
    w.win.confirm = function () { asked++; return false; };
    src.value = 'D:\\x\\third';
    src.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    await settle();
    ok(asked === 1 && recon().length === 4 && w.G.state.spec.network.host === '10.5.5.5',
      'F6: Load different… over edits asks, and Cancel keeps the session (asked ' + asked + ')');
  })();

  (function f8PlainResetFiresNothing() {
    const w = makeWorld();
    let fired = 0;
    w.win.document.addEventListener('quamgen:reset', function () { fired++; });
    click(w.win, $(w.win, 'gen-reset'));
    ok(fired === 0, 'F8: a Reset in plain Generate asks nobody to re-fill');
  })();

  // ── F10: a header Generate press answers where the user looks ────────────
  await (async function f10HeaderGenerate() {
    let release;
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } }
    ] });
    const origFetch = w.win.fetch;
    w.win.fetch = function (url, fo) {
      if (String(url).indexOf('/generate/build') >= 0) {
        w.log.push({ url: String(url), body: JSON.parse(fo.body) });
        return new w.win.Promise(function (res) {
          release = function (data) { res({ json: () => w.win.Promise.resolve(data) }); };
        });
      }
      return origFetch(url, fo);
    };
    toStep4(w.win, w.G, 2);
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\build1');
    w.G.goToStep(8);
    w.reveals.length = 0;
    click(w.win, $(w.win, 'gen-next-top'));
    await settle();
    const builds = () => w.log.filter(e => e.url.indexOf('/generate/build') >= 0);
    ok(builds().length === 1, 'F10: the header press POSTs the build');
    ok($(w.win, 'gen-next-top').disabled && $(w.win, 'gen-next').disabled,
      'F10: BOTH Generate buttons are busy during the build (top ' +
      $(w.win, 'gen-next-top').disabled + ', bottom ' + $(w.win, 'gen-next').disabled + ')');
    ok(w.reveals.indexOf('gen-build-result') >= 0,
      'F10: "Generating…" is brought into view (reveals ' + JSON.stringify(w.reveals) + ')');
    w.reveals.length = 0;
    release({ ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [] } });
    await settle();
    ok(!$(w.win, 'gen-next-top').disabled && !$(w.win, 'gen-next').disabled,
      'F10: both buttons come back after the build');
    ok(w.reveals.indexOf('gen-build-result') >= 0 &&
       $(w.win, 'gen-build-result').textContent.indexOf('Generated') >= 0,
      'F10: the result is brought into view');
  })();

  // ── r2-18: the scripts export means what the box says ────────────────────
  await (async function r18Payload() {
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/generate/build', reply: { ok: true, result: { qubits: [], qubit_pairs: [] } } }
    ] });
    toStep4(w.win, w.G, 2);
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\b1');
    const chk = $(w.win, 'gen-scripts-enable');
    chk.checked = false;
    chk.dispatchEvent(new w.win.Event('change', { bubbles: true }));
    w.G.goToStep(8);
    click(w.win, $(w.win, 'gen-next'));
    await settle();
    const b = w.log.filter(e => e.url.indexOf('/generate/build') >= 0).pop();
    ok(b && b.body.scripts_enabled === false && b.body.scripts_dir === null,
      'r2-18: an unticked export is sent as scripts_enabled:false (got ' +
      JSON.stringify(b && { e: b.body.scripts_enabled, d: b.body.scripts_dir }) + ')');
  })();

  await (async function r18EmptyFolderRefused() {
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/generate/build', reply: { ok: true, result: {} } }
    ] });
    toStep4(w.win, w.G, 2);
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\b1');
    setInput(w.win, $(w.win, 'gen-scripts-path'), '');       // the user cleared it
    w.G.goToStep(8);                                        // via the stepper
    click(w.win, $(w.win, 'gen-next'));
    await settle();
    ok(!w.log.some(e => e.url.indexOf('/generate/build') >= 0),
      'r2-18: a ticked export with no scripts folder never POSTs a build');
    ok(/scripts/i.test($(w.win, 'gen-build-result').textContent),
      'r2-18: the refusal names the scripts folder (got "' +
      $(w.win, 'gen-build-result').textContent + '")');
  })();

  (function r18RecipeLabel() {
    const w = makeWorld();
    const show = w.G._test.showBuildResult;
    const res = (script, inOut) => ({ ok: true, result: { qubits: [], qubit_pairs: [] },
                                      script: script, script_in_output: inOut });
    show(res('D:\\shared\\state_gen_scripts', false), 'D:\\x\\new');
    let t = $(w.win, 'gen-build-result').textContent;
    ok(t.indexOf('D:\\shared\\state_gen_scripts') >= 0 &&
       t.indexOf('outside the output folder') >= 0 &&
       t.indexOf('written to the output folder') < 0,
      'r2-18: a recipe written elsewhere is reported as elsewhere (got "' + t + '")');
    show(res('D:\\x\\new\\build_scripts', true), 'D:\\x\\new');
    t = $(w.win, 'gen-build-result').textContent;
    ok(t.indexOf('written inside the output folder') >= 0, 'r2-18: inside is reported as inside');
    show(res('D:\\<b>evil</b>', false), 'D:\\x\\new');
    ok(!w.win.document.querySelector('#gen-build-result b') &&
       $(w.win, 'gen-build-result').textContent.indexOf('<b>evil</b>') >= 0,
      'r2-18: the user-typed folder renders as text, never markup');
  })();

  // ── r2-20 / r2-10 / F14: the Preview-config JSON panel ─────────────────
  const PREVIEW_ROUTES = [
    { match: '/generate/envs', reply: { envs: [] } },
    { match: '/generate/preview-config', reply: {
      ok: true, config: { version: 1, elements: { q1: {} } },
      meta: { qubits: ['q1'], qubit_pairs: [], versions: {}, warnings: [] } } },
    { match: '/generate/preview-pulse-waveform', reply: {
      ok: true, element: 'coupler_q1_q2', operation: 'cz', pulse: 'cz_pulse',
      traces: [{ x: [0, 1, 2], y: [0, 0.1, 0], label: 'I' }] } },
    { match: '/generate/preview-pulses', reply: {
      ok: true, ops: [{ element: 'coupler_q1_q2', op_name: 'cz' },
                      { element: 'coupler_q2_q3', op_name: 'cz' }] } }
  ];
  // A wizard on step 8 with a finished build and its preview run.
  async function previewWorld() {
    const w = makeWorld({ routes: PREVIEW_ROUTES });
    w.trees = [];
    w.win.renderJsonTree = function (id, data, opts) {
      w.trees.push({ id: id, opts: opts });
      w.win.document.getElementById(id).textContent = JSON.stringify(data);
    };
    w.renders = [];
    w.win.PlotTheme = { houseLayout: function (l) {
      return Object.assign({ font: { color: 'rgb(1, 2, 3)' } }, l); } };
    w.win._plotlyRender = function (el, traces, layout) {
      w.renders.push({ el: el, layout: layout });
      return w.win.Promise.resolve(null);
    };
    w.G.goToStep(8);
    w.G._test.showBuildResult({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] } },
                              'D:\\x\\chipP');
    const pv = [...w.win.document.querySelectorAll('#gen-build-result button')]
      .find(b => b.textContent === 'Preview config');
    click(w.win, pv);
    await settle();
    w.panel = $(w.win, 'json-panel');
    w.jsonBtn = w.win.document.querySelector('#gen-build-result .gen-config-json-btn');
    return w;
  }

  await (async function r20PreviewDoesNotCover() {
    const w = await previewWorld();
    ok(w.win.document.querySelector('#gen-build-result .gen-config-export'),
      'r2-20: the preview result rendered (export row)');
    ok(w.panel.classList.contains('hidden'),
      'r2-20: Preview config does NOT open the JSON panel over its own result');
    ok(w.trees.length === 0, 'r2-20: nothing is rendered into the panel until asked');
    ok(w.jsonBtn && /View config JSON/.test(w.jsonBtn.textContent),
      'r2-20: a "View config JSON" button sits in the export row');
    click(w.win, w.jsonBtn);
    ok(!w.panel.classList.contains('hidden'), 'r2-20: the button opens the panel');
    ok($(w.win, 'json-panel-title').textContent === 'Generated config — chipP',
      'r2-20: titled after the built folder (got "' +
      $(w.win, 'json-panel-title').textContent + '")');
    ok(w.trees.length === 1 && w.trees[0].id === 'json-panel-tree' &&
       w.trees[0].opts.valueClick === 'copy' &&
       $(w.win, 'json-panel-tree').textContent.indexOf('elements') >= 0,
      'r2-20: the previewed config is drawn read-only (copy mode) into the tree');
  })();

  await (async function r10PanelParkedOffStep8() {
    const w = await previewWorld();
    click(w.win, w.jsonBtn);
    ok(!w.panel.classList.contains('hidden'), 'r2-10: panel open on step 8');
    w.G.goToStep(4);                                   // e.g. the stepper '4 Qubits'
    ok(w.panel.classList.contains('hidden'),
      'r2-10: leaving step 8 takes the panel off the wizard');
    ok($(w.win, 'json-panel-tree').textContent.indexOf('elements') >= 0,
      'r2-10: parked, not cleared — the tree is kept');
    w.G.goToStep(8);
    ok(!w.panel.classList.contains('hidden'), 'r2-10: back on step 8 it comes back');
    w.win.closeJsonPanel();                            // the panel's ×
    w.G.goToStep(4); w.G.goToStep(8);
    ok(w.panel.classList.contains('hidden'), 'r2-10: a panel the user closed stays closed');
    click(w.win, w.jsonBtn);
    click(w.win, $(w.win, 'gen-reset'));               // confirm() → true
    ok(w.G.state.step === 1 && w.panel.classList.contains('hidden'),
      'r2-10: Reset leaves no panel over step 1');
    w.G.goToStep(8);
    ok(w.panel.classList.contains('hidden'),
      'r2-10: after Reset the old chip\'s preview never comes back');
  })();

  await (async function f14EscapeAndGallery() {
    const w = await previewWorld();
    click(w.win, w.jsonBtn);
    const input = $(w.win, 'gen-output-path');
    input.dispatchEvent(new w.win.KeyboardEvent('keydown',
      { key: 'Escape', bubbles: true, cancelable: true }));
    ok(!w.panel.classList.contains('hidden'),
      'F14: Escape while typing in a field leaves the panel alone');
    const esc = new w.win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    w.jsonBtn.dispatchEvent(esc);
    ok(w.panel.classList.contains('hidden'), 'F14: Escape closes the JSON panel');
    ok(esc.defaultPrevented, 'F14: the Escape that closed it is consumed');
    w.G.goToStep(4); w.G.goToStep(8);
    ok(w.panel.classList.contains('hidden'), 'F14: closed by Escape = stays closed');

    const btns = w.win.document.querySelectorAll('.gen-preview-pulse-btn');
    ok(btns.length === 2, 'F14: the gallery rendered (' + btns.length + ' ops)');
    w.reveals.length = 0;
    click(w.win, btns[0]);
    await settle();
    ok(w.renders.length === 1, 'F14: the waveform was drawn');
    const lay = (w.renders[0] || {}).layout || {};
    ok(lay.font && lay.font.color === 'rgb(1, 2, 3)',
      'F14: the waveform uses the house plot theme (font ' + JSON.stringify(lay.font) + ')');
    ok(lay.xaxis && lay.xaxis.title === 'time (ns)', 'F14: its own axis titles survive the theme');
    ok(w.reveals.indexOf('gen-preview-pulses-plot') >= 0,
      'F14: the waveform is brought into view (reveals ' + JSON.stringify(w.reveals) + ')');
  })();

  // ── F8: one build at a time ──────────────────────────────────────────────
  // A wizard on step 8 with a valid spec; the build POST hangs until released.
  function buildWorld(opts) {
    const o = opts || {};
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: o.selectEnv || { ok: true } }
    ], draft: o.draft });
    const origFetch = w.win.fetch;
    w.releases = [];
    w.win.fetch = function (url, fo) {
      if (String(url).indexOf('/generate/build') >= 0) {
        w.log.push({ url: String(url), body: JSON.parse(fo.body) });
        return new w.win.Promise(function (res) {
          w.releases.push(function (data) { res({ json: () => w.win.Promise.resolve(data) }); });
        });
      }
      return origFetch(url, fo);
    };
    if (!o.draft) {
      toStep4(w.win, w.G, 2);
      w.G.state.env = 'C:/envs/test/python.exe';
      w.G.goToStep(7);
      setInput(w.win, $(w.win, 'gen-output-path'), o.out || 'D:\\x\\dbl');
      w.G.goToStep(8);
    }
    w.builds = () => w.log.filter(e => e.url.indexOf('/generate/build') >= 0);
    w.selects = () => w.log.filter(e => e.url.indexOf('/generate/select-env') >= 0);
    return w;
  }

  await (async function f8DoublePress() {
    const w = buildWorld();
    // env not persisted yet: the first press goes through /generate/select-env
    w.G.tryNext(); w.G.tryNext();          // a double press, before any answer
    await settle();
    ok(w.selects().length === 1,
      'F8: a double press during the select-env round-trip selects ONCE (got ' +
      w.selects().length + ')');
    ok(w.builds().length === 1,
      'F8: … and POSTs ONE build (got ' + w.builds().length + ')');
    w.G.tryNext();                          // a third press while it runs
    click(w.win, $(w.win, 'gen-next-top'));
    await settle();
    ok(w.builds().length === 1, 'F8: no second build while the first runs (got ' +
      w.builds().length + ')');
    ok($(w.win, 'gen-next-top').disabled && $(w.win, 'gen-next').disabled,
      'F8: both Generate buttons are busy');
    w.releases[0]({ ok: true, result: { qubits: ['q1'], qubit_pairs: [['q1', 'q2']] } });
    await settle();
    ok(!$(w.win, 'gen-next-top').disabled && !$(w.win, 'gen-next').disabled,
      'F8: the buttons come back with the answer');
    ok($(w.win, 'gen-build-result').textContent.indexOf('1 qubit and 1 pair into') >= 0,
      'F8: counts are singular where they are one (got "' +
      $(w.win, 'gen-build-result').textContent.split('\n')[0] + '")');
    w.G.tryNext();                          // a NEW press after the answer builds again
    await settle();
    ok(w.builds().length === 2, 'F8: the next press after the answer builds again');
  })();

  await (async function f8ConfirmDoublePress() {
    const w = buildWorld({ out: 'D:\\x\\full' });
    w.G.tryNext();
    await settle();
    w.releases[0]({ ok: false, needs_confirm: true, conflict_files: ['a.json'],
                    error: 'The output folder is not empty.' });
    await settle();
    const go = [...w.win.document.querySelectorAll('#gen-build-result button')]
      .find(b => b.textContent === 'Generate anyway');
    ok(!!go, 'F8: the confirm offers "Generate anyway"');
    click(w.win, go); click(w.win, go);     // double press on the confirm
    await settle();
    ok(w.builds().length === 2 && w.builds()[1].body.force === true,
      'F8: "Generate anyway" pressed twice POSTs ONE forced build (builds ' +
      w.builds().length + ')');
  })();

  await (async function f8SelectEnvFailureDoesNotLatch() {
    const w = buildWorld({ selectEnv: { ok: false, error: 'env gone' } });
    w.G.tryNext();
    await settle();
    ok(/env gone/.test($(w.win, 'gen-build-result').textContent),
      'F8: a failed select-env is reported');
    ok(!$(w.win, 'gen-next').disabled, 'F8: … and leaves Generate usable');
    w.G.tryNext();
    await settle();
    ok(w.selects().length === 2, 'F8: a failed select-env never latches the in-flight flag');
  })();

  // ── r2-13: the build outcome survives a reload / leave ───────────────────
  await (async function r13ResultRestored() {
    const w = buildWorld({ out: 'D:\\x\\kept' });
    w.G.tryNext();
    await settle();
    const pending = JSON.parse(draftOf(w.win));
    ok(pending.buildPending && pending.buildPending.outPath === 'D:\\x\\kept' &&
       !pending.lastBuild,
      'r2-13: the draft records the started build (got ' +
      JSON.stringify(pending.buildPending) + ')');
    w.releases[0]({ ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']],
                                        allocation: { big: 'x'.repeat(50) }, warnings: [] } });
    await settle();
    const d = JSON.parse(draftOf(w.win));
    ok(d.lastBuild && d.lastBuild.outPath === 'D:\\x\\kept' && !d.buildPending,
      'r2-13: the draft records the outcome');
    ok(d.lastBuild && d.lastBuild.res.result && !('allocation' in d.lastBuild.res.result),
      'r2-13: the stored outcome is trimmed (no allocation)');

    // F5 / leave and come back: a fresh mount from the same session draft
    const w2 = makeWorld({ routes: [{ match: '/generate/envs', reply: { envs: [] } }],
                           draft: draftOf(w.win) });
    const res = $(w2.win, 'gen-build-result');
    ok(w2.G.state.step === 8 && !res.hidden &&
       res.textContent.indexOf('Generated 2 qubits and 1 pair into D:\\x\\kept') >= 0,
      'r2-13: step 8 shows the finished build again (got "' + res.textContent.slice(0, 120) + '")');
    ok(!!res.querySelector('.gen-build-restored'),
      'r2-13: labelled as a restored result (the wizard may have changed since)');
    ok([...res.querySelectorAll('button')].some(b => b.textContent === 'Load into app'),
      'r2-13: the restored result still offers Load into app');
    click(w2.win, $(w2.win, 'gen-reset'));
    ok(w2.G.state.lastBuild === null && JSON.parse(draftOf(w2.win)).lastBuild === null,
      'r2-13: Reset forgets the build record');
  })();

  await (async function r13PendingNotice() {
    const w = buildWorld({ out: 'D:\\x\\lost' });
    w.G.tryNext();
    await settle();
    // F5 during the build: the answer can never arrive in this page
    const w2 = makeWorld({ routes: [{ match: '/generate/envs', reply: { envs: [] } }],
                           draft: draftOf(w.win) });
    const res = $(w2.win, 'gen-build-result');
    ok(!res.hidden && res.textContent.indexOf('D:\\x\\lost') >= 0 &&
       /outcome was not received/.test(res.textContent),
      'r2-13: a build whose answer never arrived is named (got "' + res.textContent + '")');
    ok(!$(w2.win, 'gen-next').disabled, 'r2-13: … and Generate stays usable');
  })();

  await (async function r13RemountWhileRunning() {
    const w = buildWorld({ out: 'D:\\x\\run' });
    w.G.tryNext();
    await settle();
    // the user leaves (htmx swaps the pane) and comes back before the answer
    swapAway(w.win);
    $(w.win, 'table-pane').innerHTML = HTML;
    w.G.init();
    const res = $(w.win, 'gen-build-result');
    ok(!res.hidden && /still running/.test(res.textContent) &&
       res.textContent.indexOf('D:\\x\\run') >= 0,
      'r2-13: back while the build runs, step 8 says so (got "' + res.textContent + '")');
    ok($(w.win, 'gen-next').disabled && $(w.win, 'gen-next-top').disabled,
      'r2-13: … with Generate busy (a press would be refused)');
    w.releases[0]({ ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [] } });
    await settle();
    ok(res.textContent.indexOf('Generated 2 qubits and 0 pairs into D:\\x\\run') >= 0,
      'r2-13: the answer lands in the re-mounted wizard');
    ok(!$(w.win, 'gen-next').disabled, 'r2-13: … and Generate comes back');
  })();

  await (async function r13NoDraftNoRecord() {
    // a mount with NO draft (lost / discarded) starts with no build record,
    // even though this page's memory still holds the last one
    const w = buildWorld({ out: 'D:\\x\\gone' });
    w.G.tryNext();
    await settle();
    w.releases[0]({ ok: true, result: { qubits: ['q1'], qubit_pairs: [] } });
    await settle();
    swapAway(w.win);                               // saves the draft on the way out …
    w.win.sessionStorage.removeItem(DRAFT_KEY);    // … which is then lost
    $(w.win, 'table-pane').innerHTML = HTML;
    w.G.init();
    ok($(w.win, 'gen-build-result').hidden && w.G.state.lastBuild === null,
      'r2-13: a fresh (draft-less) mount shows no stale build result');
  })();

  await (async function r13ConfirmIsNotAnOutcome() {
    const w = buildWorld({ out: 'D:\\x\\q' });
    w.G.tryNext();
    await settle();
    w.releases[0]({ ok: false, needs_confirm: true, conflict_files: [], error: 'x' });
    await settle();
    const d = JSON.parse(draftOf(w.win));
    ok(!d.lastBuild && !d.buildPending,
      'r2-13: a confirm question is neither a result nor a pending build');
  })();

  // ── generate-r2-14: "Load into app" retires the finished Generate draft ──
  function loadWorld(loadReply, opts) {
    const o = opts || {};
    const w = makeWorld({ regen: o.regen, draft: o.draft, routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/regenerate/build', reply: { ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [] } } },
      { match: '/generate/build', reply: { ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [] } } },
      { match: '/generate/load', reply: loadReply }
    ] });
    if (o.regen) {
      w.G.hydrateFromSpec(srcSpec(2), { mode: 'regenerate', buildEndpoint: '/regenerate/build',
                                        sourcePath: 'D:\\wc\\src' });
    } else {
      toStep4(w.win, w.G, 2);
    }
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\chipA');
    w.G.goToStep(8);
    w.loadBtn = () => [...$(w.win, 'gen-build-result').querySelectorAll('button')]
      .find(b => b.textContent === 'Load into app');
    return w;
  }

  await (async function r14LoadRetiresDraft() {
    const w = loadWorld({ ok: true, redirect: '#loaded' });
    w.G.tryNext();
    await settle();
    ok(!!w.loadBtn() && JSON.parse(draftOf(w.win)).step === 8,
      'r2-14 harness: built, the draft sits on step 8');
    click(w.win, w.loadBtn());
    await settle();
    ok(w.log.some(e => e.url.indexOf('/generate/load') >= 0), 'r2-14 harness: Load into app posted');
    ok(draftOf(w.win) === null,
      'r2-14: a successful Load into app retires the finished Generate draft (got ' +
      (draftOf(w.win) || '').slice(0, 60) + ')');
    pagehide(w.win);                       // the navigation's own unload
    ok(draftOf(w.win) === null, 'r2-14: … and the page leaving never writes it back');
    const w2 = makeWorld({ routes: [{ match: '/generate/envs', reply: { envs: [] } }],
                           draft: draftOf(w.win) });
    ok(w2.G.state.step === 1 && w2.G.state.spec.qubits.length === 0 &&
       $(w2.win, 'gen-build-result').hidden,
      'r2-14: the next Generate Config starts a new chip at step 1 (step ' +
      w2.G.state.step + ', qubits ' + w2.G.state.spec.qubits.length + ')');
  })();

  await (async function r14FailedLoadKeepsDraft() {
    const w = loadWorld({ ok: false, error: 'nope' });
    w.G.tryNext();
    await settle();
    click(w.win, w.loadBtn());
    await settle();
    ok(draftOf(w.win) !== null && JSON.parse(draftOf(w.win)).step === 8,
      'r2-14: a FAILED load keeps the draft (the chip is not open anywhere)');
    pagehide(w.win);
    ok(draftOf(w.win) !== null, 'r2-14: … and it is still saved on the way out');
  })();

  await (async function r14RegenLoadLeavesGenerateDraft() {
    const genDraft = (function () {
      const g = makeWorld();
      g.G.goToStep(2);
      setInput(g.win, $(g.win, 'gen-net-host'), '10.9.9.9');
      g.G.goToStep(3);
      return draftOf(g.win);
    })();
    const w = loadWorld({ ok: true, redirect: '#loaded' }, { regen: true, draft: genDraft });
    w.G.tryNext();
    await settle();
    ok(!!w.loadBtn(), 'r2-14 harness: the re-generate build offers Load into app');
    click(w.win, w.loadBtn());
    await settle();
    ok(draftOf(w.win) === genDraft,
      'r2-14: Load into app from Re-generate leaves the Generate draft alone (QA F7)');
  })();

  // ── generate-r2-18: step 1 does not accept an env that probed "missing" ───
  await (async function r18MissingEnvBlocksNext() {
    const w = makeWorld({ routes: [
      { match: '/generate/envs', reply: { envs: [
        { name: 'bad', python: 'badpy' }, { name: 'flaky', python: 'flakypy' },
        { name: 'good', python: 'goodpy' }] } },
      { match: 'python=badpy', reply: { usable: false, missing: ['quam_builder', 'quam'] } },
      { match: 'python=flakypy', reply: { usable: false, missing: [], error: 'timeout' } },
      { match: 'python=goodpy', reply: { usable: true, versions: {} } },
      { match: '/generate/select-env', reply: { ok: true } }
    ] });
    await settle();
    const row = py => w.win.document.querySelector('.gen-env-row[data-python="' + py + '"]');
    ok(!!row('badpy') && /missing/.test(row('badpy').textContent),
      'r2-18 harness: the bad env row says what it is missing');
    click(w.win, row('badpy'));
    await settle();
    ok(w.G.state.env === 'badpy' && row('badpy').classList.contains('selected'),
      'r2-18: a click still selects a ✗ row (a click is a claim — A15)');
    w.G.tryNext();
    ok(w.G.state.step === 1, 'r2-18: Next refuses an env whose probe said missing (step ' +
      w.G.state.step + ')');
    ok(/missing quam_builder, quam/.test($(w.win, 'gen-message').textContent),
      'r2-18: … and names what is missing (got "' + $(w.win, 'gen-message').textContent + '")');
    click(w.win, row('flakypy'));
    await settle();
    w.G.tryNext();
    ok(w.G.state.step === 2, 'r2-18: a failed probe stays fail-open (step ' + w.G.state.step + ')');
    w.G.goToStep(1);
    click(w.win, row('goodpy'));
    await settle();
    w.G.tryNext();
    ok(w.G.state.step === 2, 'r2-18: a ✓ env moves on');
  })();

  // ── F21: Review flags a relative output / scripts path ───────────────────
  (function f21ReviewFlagsRelative() {
    const w = makeWorld();
    toStep4(w.win, w.G, 2);
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'gen_out\\rel1');
    w.G.goToStep(8);                       // a step-rail jump: no step-7 guard
    const rev = () => $(w.win, 'gen-review').textContent;
    ok(w.G.state.step === 8, 'F21 harness: the jump reached Review');
    ok(rev().indexOf('gen_out\\rel1  ⚠ not an absolute path — Generate will refuse it') >= 0,
      'F21: Review flags the relative output folder (got "' + rev().slice(-260) + '")');
    const tdOf = label => [...$(w.win, 'gen-review').querySelectorAll('tr')]
      .find(tr => tr.querySelector('th').textContent === label).querySelector('td');
    ok(tdOf('Output folder').classList.contains('gen-cap-degrade'), 'F21: … in the warning style');
    ok(tdOf('Python scripts').textContent.indexOf('not an absolute path') >= 0,
      'F21: the scripts folder that followed it is flagged too (got "' +
      tdOf('Python scripts').textContent + '")');
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\abs\\out');
    w.G.goToStep(8);
    ok(rev().indexOf('not an absolute path') < 0 && tdOf('Output folder').textContent === 'D:\\abs\\out' &&
       !tdOf('Output folder').classList.contains('gen-cap-degrade'),
      'F21: an absolute folder is listed plainly');
  })();

  // ── F20: the user's own re-generate is named, and its report comes back ──
  await (async function f20OwnBuildReport() {
    const w = buildWorld({ out: 'D:\\x\\v_f20' });
    w.G.tryNext();
    await settle();
    const report = { ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [['q1', 'q2']], warnings: [] },
                     merge: { carried: 7, grafted: 1, residual_lost: [], residual_lost_total: 0 } };
    w.releases[0]({ ok: false, needs_confirm: true, existing_chip: true, conflict_files: [],
                    error: 'This folder holds the chip State Manager re-generated here at 2026-09-24 17:08',
                    own_build: { built_at: '2026-09-24T17:08:11+09:00', source_folder: 'D:\\wc\\src',
                                 report: report } });
    await settle();
    const res = $(w.win, 'gen-build-result');
    const btns = () => [...res.querySelectorAll('button')];
    const show = btns().find(b => b.textContent === "Show that build's report");
    ok(!!show && btns().some(b => b.textContent === 'Generate anyway'),
      'F20: the confirm offers the build\'s report beside "Generate anyway"');
    click(w.win, show);
    ok(res.textContent.indexOf('Generated 2 qubits and 1 pair into D:\\x\\v_f20') >= 0 &&
       !!res.querySelector('.gen-build-restored') &&
       res.querySelector('.gen-build-restored').textContent.indexOf('2026-09-24 17:08') >= 0,
      'F20: the report is shown again, labelled with its time (got "' +
      res.textContent.slice(0, 200) + '")');
    ok(w.builds().length === 1, 'F20: showing the report builds nothing');

    const w2 = buildWorld({ out: 'D:\\x\\other' });
    w2.G.tryNext();
    await settle();
    w2.releases[0]({ ok: false, needs_confirm: true, existing_chip: true, conflict_files: [],
                     error: 'This folder already contains a chip' });
    await settle();
    ok(![...$(w2.win, 'gen-build-result').querySelectorAll('button')]
         .some(b => /report/.test(b.textContent)),
      'F20: a foreign chip offers no report');
  })();

  await (async function f20ReloadMidRegenBuildAsks() {
    const w = makeWorld({ regen: true, routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } }
    ] });
    w.G.hydrateFromSpec(srcSpec(2), { mode: 'regenerate', buildEndpoint: '/regenerate/build',
                                      sourcePath: 'D:\\wc\\src' });
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\v_f20');
    w.G.goToStep(8);
    ok(!unload(w.win).defaultPrevented, 'F20 harness: an untouched session leaves freely');
    w.G.tryNext();
    await settle();
    ok(w.log.some(e => e.url.indexOf('/regenerate/build') >= 0), 'F20 harness: the build is running');
    ok(unload(w.win).defaultPrevented,
      'F20: F5 / close during a re-generate build asks first (its report reaches only this page)');

    const g = buildWorld({ out: 'D:\\x\\plain' });
    g.G.tryNext();
    await settle();
    ok(!unload(g.win).defaultPrevented,
      'F20: plain Generate is unchanged (its draft already says a build never answered)');
  })();

  // ── regenerate-r2-35: a source changed under the wizard is named first ───
  await (async function r35SourceChanged() {
    let recons = 0;
    const w = makeWorld({ regenPage: true, routes: [
      { match: '/generate/envs', reply: { envs: [] } },
      { match: '/generate/select-env', reply: { ok: true } },
      { match: '/regenerate/reconstruct', reply: function () {
        recons += 1;
        return { ok: true, spec: srcSpec(2), source_folder: 'D:\\wc\\key123',
                 source_hash: 'hash-' + recons,
                 source_name: 'LOADED_CHIP', notes: [], info_notes: [] };
      } }
    ] });
    const origFetch = w.win.fetch;
    const releases = [];
    w.win.fetch = function (url, fo) {
      if (String(url).indexOf('/regenerate/build') >= 0) {
        w.log.push({ url: String(url), body: JSON.parse(fo.body) });
        return new w.win.Promise(function (res) {
          releases.push(function (data) { res({ json: () => w.win.Promise.resolve(data) }); });
        });
      }
      return origFetch(url, fo);
    };
    await settle();
    ok(w.G.state.regenSourceHash === 'hash-1', 'r2-35: the hydrate keeps the source stamp');
    w.G.state.env = 'C:/envs/test/python.exe';
    w.G.goToStep(7);
    setInput(w.win, $(w.win, 'gen-output-path'), 'D:\\x\\d2_race');
    w.G.goToStep(8);
    w.G.tryNext();
    await settle();
    const builds = () => w.log.filter(e => e.url.indexOf('/regenerate/build') >= 0);
    ok(builds().length === 1 && builds()[0].body.source_hash === 'hash-1' &&
       builds()[0].body.ack_source_changed === false,
      'r2-35: the build carries the stamp, unacknowledged (got ' +
      JSON.stringify(builds()[0] && { h: builds()[0].body.source_hash,
                                      a: builds()[0].body.ack_source_changed }) + ')');
    releases[0]({ ok: false, needs_confirm: true, confirm_kind: 'source_changed',
                  error: 'The source chip changed since this wizard read it',
                  source_drift: [
                    { group: 'qubit', id: 'q1', field: 'anharmonicity', shown: -200607661.787,
                      now: -190000000, yours: false },
                    { group: 'resonator', id: 'q2', field: 'RF_freq', shown: 7.1e9, now: 7.2e9,
                      yours: true },
                    { group: 'pairs', id: 'q1-q2', field: 'moving_qubit', shown: 'target',
                      now: 'control', yours: false }], source_drift_total: 5 });
    await settle();
    const res = $(w.win, 'gen-build-result');
    ok(res.textContent.indexOf('qubit q1 anharmonicity: shown -0.200607661787 GHz → chip now ' +
       '-0.19 GHz — the chip\'s value is built') >= 0,
      'r2-35: each drifted value is named, in the Populate step\'s units, with what gets ' +
      'built (got "' + res.textContent.slice(0, 300) + '")');
    ok(res.textContent.indexOf('resonator q2 RF_freq: shown 7.1 GHz → chip now 7.2 GHz ' +
       '— you edited it here, so your value is built') >= 0,
      'r2-35: a value the user also edited says the wizard value wins');
    ok(res.textContent.indexOf('pairs q1-q2 moving_qubit: shown target → chip now control') >= 0 &&
       res.textContent.indexOf('… and 2 more') >= 0,
      'r2-35: a unitless value reads as is; the list says how many more there are');
    const btn = t => [...res.querySelectorAll('button')].find(b => b.textContent === t);
    ok(!!btn('Generate anyway') && !!btn('Reload the wizard from the chip'),
      'r2-35: the question offers both ways out');
    click(w.win, btn('Generate anyway'));
    await settle();
    ok(builds().length === 2 && builds()[1].body.ack_source_changed === true &&
       builds()[1].body.source_hash === 'hash-1',
      'r2-35: "Generate anyway" re-posts with the acknowledgement');
    releases[1]({ ok: false, needs_confirm: true, conflict_files: [],
                  error: 'This folder already contains a chip' });
    await settle();
    click(w.win, btn('Generate anyway'));
    await settle();
    ok(builds().length === 3 && builds()[2].body.ack_source_changed === true &&
       builds()[2].body.force === true,
      'r2-35: acking the overwrite keeps the source acknowledgement (two gates, two acks)');
    releases[2]({ ok: false, needs_confirm: true, confirm_kind: 'source_changed',
                  error: 'changed', source_drift: [], source_drift_total: 0 });
    await settle();
    click(w.win, btn('Reload the wizard from the chip'));
    await settle();
    ok(recons === 2 && w.G.state.regenSourceHash === 'hash-2' && w.G.state.step === 8 &&
       w.G.state.mode === 'regenerate',
      'r2-35: "Reload the wizard" re-reads the source and returns to step 8 (reconstructs ' +
      recons + ', hash ' + w.G.state.regenSourceHash + ', step ' + w.G.state.step + ')');
    ok(res.hidden && !res.querySelector('button'),
      'r2-35: … and the answered question is gone (no stale "Generate anyway" to ack the new read)');
    w.G.tryNext();
    await settle();
    ok(builds().length === 4 && builds()[3].body.source_hash === 'hash-2' &&
       builds()[3].body.ack_source_changed === false,
      'r2-35: after the reload the next build carries the NEW stamp, unacknowledged');
    releases[3]({ ok: false, needs_confirm: true, confirm_kind: 'source_changed',
                  error: 'changed', source_drift: [], source_drift_total: 0 });
    await settle();
    click(w.win, btn('Generate anyway'));
    await settle();
    releases[4]({ ok: true, result: { qubits: ['q1', 'q2'], qubit_pairs: [] } });
    await settle();
    w.G.tryNext();
    await settle();
    ok(builds().length === 6 && builds()[4].body.ack_source_changed === true &&
       builds()[5].body.ack_source_changed === false,
      'r2-35: a finished build consumes the acknowledgement (the next one asks again)');
    click(w.win, $(w.win, 'gen-reset'));
    ok(w.G.state.regenSourceHash === null,
      'r2-35: Reset drops the stamp with the regen mode (got ' + w.G.state.regenSourceHash + ')');
  })();

  await (async function r35PlainGenerateSendsNoStamp() {
    const w = buildWorld({ out: 'D:\\x\\plain2' });
    w.G.tryNext();
    await settle();
    ok(w.builds()[0].body.source_hash === null, 'r2-35: plain Generate sends no source stamp');
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
