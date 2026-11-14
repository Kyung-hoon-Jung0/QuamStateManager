// Behavioral check: step-5 wiring and step-6 pair-table feedback from a
// from-scratch build of a fixed-frequency + tunable-coupler chip.
//
//   pin forms: a Pin accepts "con1/1/2" and "1/1/2"; "con1/9/1", "1/1", "x/1/2" are
//       refused, and the hint names both accepted forms
//   fresh feedline ports: a feedline Out change shows the new port on the card summary and in
//       the Auto-allocated column at once (never the old allocation's port)
//   stranded check: "Checking wiring" never outlives a run that was stranded
//   default CZ shape: a per-pair "CZ shape" select left untouched shows the default shape
//
// Run: node tests/generate_wiring_feedback_selfcheck.cjs   (needs jsdom)
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

async function world(answer) {
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
  const calls = [];
  win.fetch = function (url, opts) {
    calls.push(url);
    if (answer && answer[url] !== undefined) {
      return win.Promise.resolve({ json: function () { return win.Promise.resolve(answer[url]); } });
    }
    if (answer && answer.ok && url === '/generate/allocate' && calls.filter(u => u === url).length === 1) {
      return win.Promise.resolve({ json: function () { return win.Promise.resolve(answer); } });
    }
    return new win.Promise(function () {});   // later runs never answer
  };
  new win.Function(GEN_JS).call(win);
  await new Promise(function (r) { win.setTimeout(r, 0); });   // let mount timers settle first
  const G = win.QuamGen, T = G._test, state = G.state;
  state.spec.instruments.controllers = [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 3, fem: 'lf' }] }];
  state.spec.network = { host: '1.2.3.4', cluster_name: 'C', port: null };
  state.spec.qubits = ['q1', 'q2'];
  win.document.getElementById('gen-mux-size').value = '5';
  state.env = 'python';
  T.deriveLines();
  return { win, T, state, calls, doc: win.document };
}

function tick(win) { return new Promise(function (r) { win.setTimeout(r, 0); }); }

(async function main() {
  // [pin forms]
  {
    const w = await world(null);
    const T = w.T;
    const a = T.pinToChannel('con1/1/2', 'drive');
    ok(a && a.con === 1 && a.slot === 1 && a.out_port === 2, '[pin forms] "con1/1/2" is a pin: ' + JSON.stringify(a));
    const b = T.pinToChannel('1/1/2', 'drive');
    ok(b && b.con === 1 && b.slot === 1 && b.out_port === 2, '[pin forms] "1/1/2" is a pin');
    const c = T.pinToChannel('con2/3/8', 'flux');
    ok(c && c.con === 2 && c.out_slot === 3 && c.out_port === 8, '[pin forms] "con2/3/8" is a pin');
    ['con1/9/1', '1/1', 'x/1/2', 'con/1/2', 'con0/1/1', 'cone1/1/1'].forEach(function (p) {
      ok(T.pinToChannel(p, 'drive') === null, '[pin forms] "' + p + '" is refused');
    });
    ok(/con1\/1\/2/.test(T.PIN_HINT) && /(^|[^n\d])1\/1\/2/.test(T.PIN_HINT.replace('con1/1/2', '')),
       '[pin forms] the hint names both accepted forms: ' + T.PIN_HINT);
    const intro = w.doc.querySelector('[data-step="5"] p.muted');
    ok(intro && intro.textContent.indexOf('con1/1/2') >= 0, '[pin forms] the step-5 intro shows the accepted form');
  }

  // [fresh feedline ports]
  {
    const answer = { ok: true, result: { allocation: {
      q1: { rr: [{ con: 1, slot: 1, port: 8, io_type: 'output', instrument_id: 'mw-fem' },
                 { con: 1, slot: 1, port: 1, io_type: 'input', instrument_id: 'mw-fem' }] },
      q2: { rr: [{ con: 1, slot: 1, port: 8, io_type: 'output', instrument_id: 'mw-fem' },
                 { con: 1, slot: 1, port: 1, io_type: 'input', instrument_id: 'mw-fem' }] } } } };
    const w = await world(answer);
    const T = w.T, state = w.state;
    state.step = 5;
    T.renderWiringTable();
    T.runAutoAllocate(true);
    await tick(w.win); await tick(w.win); await tick(w.win);
    ok(state.allocation && state.allocation.q1, '[fresh feedline ports] setup: the first allocation was adopted');
    const card = () => (w.doc.querySelector('.gen-ro-alloc') || {}).textContent || '';
    ok(card().indexOf('Out 8') >= 0, '[fresh feedline ports] setup: the card says Out 8: ' + card());
    const name = T.readoutModel().feeds[0].name;
    const before = (state.spec.lines.find(l => l.element === 'q2' && l.line === 'resonator') || {}).channel;
    T.setFeedlineOutput(name, { con: 1, slot: 1 }, 1);
    ok(card().indexOf('Out 8') < 0 && card().indexOf('Out 1') >= 0,
       '[fresh feedline ports] the card summary follows Out 1 at once: ' + card());
    const cells = Array.from(w.doc.querySelectorAll('#gen-wiring-tbl tr'))
      .filter(tr => /resonator/.test(tr.textContent))
      .map(tr => tr.querySelector('.gen-wiring-alloc').textContent);
    ok(cells.length && cells.every(t => t.indexOf('p8') < 0),
       '[fresh feedline ports] the Auto-allocated column drops the old port: ' + JSON.stringify(cells));
    ok(before !== undefined, '[fresh feedline ports] setup: q2 has a resonator line');
  }

  // [stranded check]
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    state.step = 5;
    T.runAutoAllocate(true);
    const st = w.doc.getElementById('gen-allocate-status');
    ok(/Checking wiring/.test(st.textContent), '[stranded check] setup: a run is in flight');
    T.resetAllocRuntime();
    ok(!/Checking wiring/.test(st.textContent), '[stranded check] a stranded run leaves no "Checking wiring": ' + st.textContent);
  }

  // [default CZ shape]
  {
    const w = await world(null);
    const T = w.T;
    const col = T.POP_COUPLER_ONLY_PAIR_COLS.find(c => c.field === 'coupler_pulse_shape');
    ok(col, '[default CZ shape] setup: the CZ shape column exists');
    const el = T.buildPopCell('pairs', 'q1-q2', col);
    const sel = el.tagName === 'SELECT' ? el : el.querySelector('select');
    ok(sel && sel.value === 'flattop', '[default CZ shape] an untouched CZ shape shows flattop: ' + (sel && sel.value));
  }

  // [mixed inputs]: two feedlines on Out 1/8 with different input relations -> "mixed"
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    state.spec.qubits = ['q1', 'q2', 'q3', 'q4'];
    w.doc.getElementById('gen-mux-size').value = '2';
    T.deriveLines();
    const names = T.readoutModel().feeds.map(f => f.name);
    ok(names.length === 2, '[mixed inputs] setup: two feedlines: ' + names);
    T.setFeedlineOutput(names[0], { con: 1, slot: 1 }, 1);
    T.setFeedlineInput(names[0], 'neighbor');
    T.setFeedlineOutput(names[1], { con: 1, slot: 1 }, 8);
    T.setFeedlineInput(names[1], 'crossing');
    T.renderReadoutPool();
    const on = w.doc.querySelectorAll('.gen-ro-mode.is-on').length;
    const mixed = w.doc.querySelector('[data-gen-ro-mixed]');
    ok(on === 0 && mixed && mixed.textContent === 'mixed', '[mixed inputs] disagreeing cards: no option pressed, "mixed" shown');
    T.setAllReadoutInputs('neighbor');
    ok(!w.doc.querySelector('[data-gen-ro-mixed]') && w.doc.querySelectorAll('.gen-ro-mode.is-on').length === 1,
       '[mixed inputs] pressing an option sets every card and clears "mixed"');
  }

  // [feedline badge]: a Pin box filled by a feedline card carries "from the feedline" until typed in
  {
    const w = await world(null);
    const T = w.T;
    const name = T.readoutModel().feeds[0].name;
    T.setFeedlineOutput(name, { con: 1, slot: 1 }, 1);
    T.renderWiringTable();
    const row = () => Array.from(w.doc.querySelectorAll('#gen-wiring-tbl tr'))
      .find(tr => /q1/.test(tr.children[0] && tr.children[0].textContent) && /resonator/.test(tr.textContent));
    ok(row() && row().querySelector('[data-gen-pin-from-card]'), '[feedline badge] a card-set pin shows the badge');
    const box = row().querySelector('.gen-wiring-pin');
    box.value = '1/1/8';
    box.dispatchEvent(new w.win.Event('change'));
    T.renderWiringTable();
    ok(row() && !row().querySelector('[data-gen-pin-from-card]'), '[feedline badge] after typing a pin the badge is gone');
  }

  // [coupler-only note]: coupler-only chip, step 6 note never sends the user to step 4 for Flux
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    state.chipArch = 'fixed_tunable_coupler_lab';
    state.qubitFlux = false;
    state.spec.qubit_pairs = [['q1', 'q2']];
    T.renderPopulateTables();
    const note = w.doc.getElementById('gen-pop-hidden-note');
    ok(note && !note.hidden && /no qubit has a flux line/.test(note.textContent) &&
       /Qubit pairs, 2Q gates/.test(note.textContent) && !/Change line types/.test(note.textContent),
       '[coupler-only note] coupler-only note: ' + (note && note.textContent));
    // [empty optional fields]: empty optional coupler-model fields carry no "valid" mark
    const fields = Array.from(w.doc.querySelectorAll('input[data-field]'))
      .filter(el => el.closest('#gen-pop-sec-pairs, #table-pane') && /coupler|flux|coupling|source/i.test(el.dataset.field));
    ok(fields.length > 0, '[empty optional fields] setup: coupler-model inputs rendered');
    ok(fields.every(el => el.getAttribute('aria-invalid') !== 'false'),
       '[empty optional fields] no coupler-model field says aria-invalid="false"');
  }

  // [scripts separator]: the scripts folder keeps the separator the user typed
  {
    const w = await world(null);
    const T = w.T;
    ok(T.autoScriptsPath('D:/x/quam_state') === 'D:/x/quam_state/state_gen_scripts', '[scripts separator] forward slashes stay: ' + T.autoScriptsPath('D:/x/quam_state'));
    ok(T.autoScriptsPath('D:\\x\\quam_state') === 'D:\\x\\quam_state\\state_gen_scripts', '[scripts separator] backslashes stay');
  }

  // [focused pin box]: a focused, untouched resonator Pin box survives a re-render + blur
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    T.renderWiringTable();
    const box = () => w.doc.querySelector('input[aria-label="q2 resonator pin"]');
    box().focus();
    const name = T.readoutModel().feeds[0].name;
    T.setFeedlineOutput(name, { con: 1, slot: 1 }, 1);   // re-renders the table
    const q2 = () => state.spec.lines.find(l => l.element === 'q2' && l.line === 'resonator');
    const pinBefore = T.channelToPin(q2().channel);
    ok(pinBefore === '1/1/1', '[focused pin box] setup: the card pinned q2: ' + pinBefore);
    ok(box().value === pinBefore, '[focused pin box] the focused box shows the stored pin after the re-render: "' + box().value + '"');
    box().dispatchEvent(new w.win.Event('blur'));
    ok(T.channelToPin(q2().channel) === pinBefore, '[focused pin box] blurring the untouched box keeps the pin: ' + JSON.stringify(q2().channel));
    ok(state.pinFromCard.q2 === true, '[focused pin box] ...and keeps its "from the feedline" origin');
    // A real browser blurs the focused box while the re-render removes it,
    // i.e. after the card changed the pin but before the new box exists.
    const old = box();
    old.focus();
    q2().channel = Object.assign({}, q2().channel, { out_port: 8 });   // the card's new pin
    old.dispatchEvent(new w.win.Event('blur'));
    ok(T.channelToPin(q2().channel) === '1/1/8' && state.pinFromCard.q2 === true,
       '[focused pin box] a blur of the stale box during a re-render commits nothing: ' + JSON.stringify(q2().channel));
  }

  // [badge after reload]: the badge origin survives a reload (draft save + restore)
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    const name = T.readoutModel().feeds[0].name;
    T.setFeedlineOutput(name, { con: 1, slot: 1 }, 1);
    T.renderWiringTable();
    const box = w.doc.querySelector('input[aria-label="q1 resonator pin"]');
    box.value = '1/1/8';
    box.dispatchEvent(new w.win.Event('change'));
    T.saveDraft();
    const draft = JSON.parse(w.win.sessionStorage.getItem('quam_generate_draft'));
    const w2 = await world(null);
    w2.T.applyDraft(draft);
    w2.T.renderWiringTable();
    const row = q => Array.from(w2.doc.querySelectorAll('#gen-wiring-tbl tr'))
      .find(tr => tr.children[0] && tr.children[0].textContent === q && /resonator/.test(tr.textContent));
    ok(row('q1') && !row('q1').querySelector('[data-gen-pin-from-card]'), '[badge after reload] a typed pin has no badge after a reload');
    ok(row('q2') && row('q2').querySelector('[data-gen-pin-from-card]'), '[badge after reload] a card-set pin keeps its badge after a reload');
  }

  // [typed pin origin]: a typed pin keeps its own origin when the card assigns another
  // member; Ctrl+Z takes the new member's pin and badge back together
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    state.spec.qubits = ['q1', 'q2', 'q3'];
    w.doc.getElementById('gen-mux-size').value = '2';
    T.deriveLines();
    const feed = T.readoutModel().feeds[0].name;
    T.setFeedlineOutput(feed, { con: 1, slot: 1 }, 1);
    T.renderWiringTable();
    const q1box = w.doc.querySelector('input[aria-label="q1 resonator pin"]');
    q1box.value = '1/1/8';
    q1box.dispatchEvent(new w.win.Event('change'));
    ok(!state.pinFromCard.q1, '[typed pin origin] setup: q1 typed');
    const q3 = () => state.spec.lines.find(l => l.element === 'q3' && l.line === 'resonator');
    const q3before = JSON.stringify(q3().channel);
    T.assignReadout(['q3'], feed);
    ok(!state.pinFromCard.q1, '[typed pin origin] assigning q3 leaves typed q1 without the badge');
    ok(state.pinFromCard.q3 === true, '[typed pin origin] q3, written by the card, has it');
    ok(w.win._wizUndo.tryUndo() === true, '[typed pin origin] Ctrl+Z consumed');
    ok(JSON.stringify(q3().channel) === q3before && !state.pinFromCard.q3,
       '[typed pin origin] Ctrl+Z restores q3 pin and origin together: ' + JSON.stringify(q3().channel));
  }

  // [no-pairs note]: coupler-only chip without pairs: the note never points at a hidden section
  {
    const w = await world(null);
    const T = w.T, state = w.state;
    state.chipArch = 'fixed_tunable_coupler_lab';
    state.qubitFlux = false;
    state.spec.qubit_pairs = [];
    T.renderPopulateTables();
    const note = w.doc.getElementById('gen-pop-hidden-note').textContent;
    ok(!/Qubit pairs, 2Q gates/.test(note) && /Define a pair in step 4 to enter coupler values/.test(note) &&
       !/Change line types/.test(note),
       '[no-pairs note] no-pairs note: ' + note);
  }

  // [custom interpreter versions]: a custom interpreter that probes usable shows its package versions
  {
    const w = await world({ '/generate/probe': { usable: true,
      versions: { qualang_tools: '0.24.0', quam_builder: '0.6.0', quam: '0.6.0' } } });
    w.doc.getElementById('gen-env-custom-path').value = 'C:/some/venv/python.exe';
    w.T.useCustomEnv();
    await tick(w.win); await tick(w.win);
    const st = w.doc.getElementById('gen-env-custom-status').textContent;
    ok(/qualang_tools 0\.24\.0/.test(st) && /quam_builder 0\.6\.0/.test(st) && /quam 0\.6\.0/.test(st),
       '[custom interpreter versions] custom interpreter status names the versions: ' + st);
  }

  console.log(fails ? fails + ' FAILED of ' + asserts : 'all checks passed (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})();
