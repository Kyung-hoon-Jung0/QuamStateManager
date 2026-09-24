// Wizard Ctrl+Z for STRUCTURAL edits (QA regenerate-r2-30).
//
// The wizard's undo stack recorded only `change` events on fields still in
// the page. A step-4 pair Control/Target pick re-renders its own row before
// the document listener sees the event (so nothing was recorded), and
// "+ Add pair", the pair × and a step-5 wiring drag never fire `change` at
// all -- Ctrl+Z answered "Nothing to undo in the wizard." (or silently undid
// an OLDER, unrelated field) and the change stayed.
//
// Drives the real _generate.html + generate.js; Ctrl+Z is window._wizUndo
// .tryUndo(), the call app.js's global handler makes while the wizard is
// mounted (pinned separately by wiz_undo_selfcheck.cjs).
//
// Run: node tests/generate_wizundo_struct_selfcheck.cjs   (needs jsdom)
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

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }
const flush = () => new Promise(r => setImmediate(r));
async function settle(n) { for (let i = 0; i < (n || 6); i++) await flush(); }

const ALLOC = () => ({
  q1: { xy: [{ con: 1, slot: 1, port: 2, io_type: 'output' }],
        rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] },
  q2: { xy: [{ con: 1, slot: 1, port: 3, io_type: 'output' }],
        rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] },
  q3: { xy: [{ con: 1, slot: 1, port: 4, io_type: 'output' }],
        rr: [{ con: 1, slot: 1, port: 1, io_type: 'output' }, { con: 1, slot: 1, port: 1, io_type: 'input' }] }
});

function makeWorld() {
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
  win.renderInstrumentWiring = function () {};   // stub: keeps planted ports
  win.confirm = function () { return true; };
  const toasts = [];
  win.showToast = function (m) { toasts.push(String(m)); };
  const log = [];
  win.fetch = function (url, opts) {
    log.push(String(url));
    if (String(url).indexOf('/generate/allocate') >= 0) {
      return win.Promise.resolve({ json: () => win.Promise.resolve({ ok: true, result: { allocation: ALLOC() } }) });
    }
    return new win.Promise(function () {});
  };
  win.__drop = null;   // the cell elementFromPoint "finds" on a drop
  win.document.elementFromPoint = function () { return win.__drop; };
  new win.Function(TOPO_JS).call(win);
  new win.Function(GEN_JS).call(win);
  return { win, toasts, log };
}

function setInput(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}
// a committed field edit the way a person makes one (focusin snapshot first)
function typeField(win, el, value) {
  el.dispatchEvent(new win.FocusEvent('focusin', { bubbles: true }));
  setInput(win, el, value);
}

// 3 qubits, CZ via a tunable coupler, pairs q1-q2 + q2-q3, at step 4.
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
  G.state.spec.qubit_pairs = [['q1', 'q2'], ['q2', 'q3']];
  G.state.pairsTouched = true;
  G.goToStep(4);
  return G;
}
const pairsOf = G => G.state.spec.qubit_pairs.map(p => p[0] + '-' + p[1]).join(',');
const undo = win => win._wizUndo.tryUndo();

(async function main() {
  // ── U1: a Control/Target pick is undone, CZ manual flag with it ─────────────
  {
    const { win, toasts } = makeWorld();
    const G = buildWizard(win);
    const tSel = win.document.querySelectorAll('#gen-pair-list .gen-pair-t')[0];
    typeField(win, tSel, 'q3');
    ok(pairsOf(G) === 'q1-q3,q2-q3', 'U1: the pick changed pair 1 (' + pairsOf(G) + ')');
    const czPinned = ((G.state.spec.populate.pairs || {})['q1-q3'] || {}).cz_order === 'manual';
    ok(undo(win) === true, 'U1: Ctrl+Z is consumed');
    ok(pairsOf(G) === 'q1-q2,q2-q3', 'U1: Ctrl+Z restores the pair (' + pairsOf(G) + ')');
    ok(!/Nothing to undo/.test(toasts.join('|')) && /Undid: pair q1–q2 change/.test(toasts.join('|')),
      'U1: the toast names what was undone (got ' + JSON.stringify(toasts) + ')');
    const sel = win.document.querySelectorAll('#gen-pair-list .gen-pair-t')[0];
    ok(sel && sel.value === 'q2', 'U1: the dropdown shows q2 again');
    ok(!czPinned || !('q1-q3' in (G.state.spec.populate.pairs || {})),
      'U1: the manual-orientation flag the pick set is gone with it');
  }

  // ── U2: + Add pair and the × are undone; the line set follows ───────────────
  {
    const { win } = makeWorld();
    const G = buildWizard(win);
    const couplers = () => G.state.spec.lines.filter(l => l.line === 'coupler').length;
    win.document.getElementById('gen-add-pair').dispatchEvent(new win.Event('click', { bubbles: true }));
    ok(G.state.spec.qubit_pairs.length === 3 && couplers() === 3, 'U2: + Add pair added a pair + its line (' + couplers() + ')');
    undo(win);
    ok(pairsOf(G) === 'q1-q2,q2-q3', 'U2: Ctrl+Z removes the added pair (' + pairsOf(G) + ')');
    ok(couplers() === 2, 'U2: ...and its coupler line (' + couplers() + ' coupler lines)');
    // × on pair 2 with a pinned coupler line: undo brings the pair AND its pin back
    const cl = G.state.spec.lines.find(l => l.line === 'coupler' && l.element === 'q2-q3');
    ok(!!cl, 'U2: the q2-q3 coupler line exists');
    if (cl) cl.channel = { kind: 'lf_fem', con: 1, out_slot: 2, out_port: 7 };
    win.document.querySelectorAll('#gen-pair-list .gen-row-del')[1]
      .dispatchEvent(new win.Event('click', { bubbles: true }));
    ok(pairsOf(G) === 'q1-q2', 'U2: × deleted pair 2');
    undo(win);
    const back = G.state.spec.lines.find(l => l.line === 'coupler' && l.element === 'q2-q3');
    ok(pairsOf(G) === 'q1-q2,q2-q3' && back && back.channel && back.channel.out_port === 7,
      'U2: Ctrl+Z restores the deleted pair with its coupler pin (' + JSON.stringify(back && back.channel) + ')');
  }

  // ── U3: field, then pair pick -> Ctrl+Z twice walks back LIFO ───────────────
  {
    const { win } = makeWorld();
    const G = buildWizard(win);
    const mux = win.document.getElementById('gen-mux-size');
    const before = mux.value;
    typeField(win, mux, '2');
    typeField(win, win.document.querySelectorAll('#gen-pair-list .gen-pair-c')[1], 'q1');
    ok(pairsOf(G) === 'q1-q2,q1-q3', 'U3: pair 2 control picked (' + pairsOf(G) + ')');
    undo(win);
    ok(pairsOf(G) === 'q1-q2,q2-q3' && mux.value === '2',
      'U3: the FIRST Ctrl+Z undoes the pair, not the older field (mux ' + mux.value + ')');
    undo(win);
    ok(mux.value === before, 'U3: the second Ctrl+Z undoes the field (mux ' + mux.value + ')');
  }

  // ── U4: a content swap makes a pair snapshot stale (never replayed) ─────────
  {
    const { win, toasts } = makeWorld();
    const G = buildWizard(win);
    typeField(win, win.document.querySelectorAll('#gen-pair-list .gen-pair-t')[0], 'q3');
    G.hydrateFromSpec({
      network: { host: '1.2.3.4', cluster_name: 'C' },
      instruments: { controllers: [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }], opx_plus: [], octaves: [] },
      qubits: ['q7', 'q8'], qubit_pairs: [['q7', 'q8']], twpas: [], lines: [], populate: { qubits: {}, pairs: {} }
    }, { mode: 'regenerate' });
    undo(win);
    // (an OLDER field entry -- step 4's qubit count -- may apply instead:
    // field entries are not scoped to the chip; separate from this pin)
    ok(pairsOf(G).indexOf('q1-') < 0 && pairsOf(G).indexOf('q2-') < 0,
      'U4: an old chip\'s pair snapshot is not replayed onto a new chip (' + pairsOf(G) + ')');
    ok(!/Undid: pair/.test(toasts.join('|')), 'U4: ...it is skipped (got ' + JSON.stringify(toasts) + ')');
  }

  // ── U5: a step-5 wiring drag is undone ──────────────────────────────────────
  {
    const { win, toasts, log } = makeWorld();
    const G = buildWizard(win);
    G.state.env = 'C:/envs/test/python.exe';
    G.goToStep(5); await settle();
    ok(!!G.state.allocation && log.some(u => u.indexOf('/generate/allocate') >= 0), 'U5: step 5 allocated');
    const di = G.state.spec.lines.findIndex(l => l.element === 'q1' && l.line === 'drive');
    const chBefore = JSON.stringify(G.state.spec.lines[di].channel || null);
    const host = win.document.getElementById('gen-wiring-diagram');
    host.innerHTML =
      '<div class="iw-port" data-con="1" data-slot="1" data-port="2" data-io="output">' +
      '<span class="iw-port-circle" data-element="q1" data-role="xy"></span></div>' +
      '<div class="iw-port" id="tgt" data-con="1" data-slot="1" data-port="6" data-io="output"></div>';
    G._test.attachWiringDrag();
    function drag() {
      const circle = host.querySelector('.iw-port-circle[data-element="q1"]');
      circle.dispatchEvent(new win.MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: 5, clientY: 5 }));
      win.__drop = host.querySelector('#tgt');
      win.document.dispatchEvent(new win.MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: 9, clientY: 9 }));
      win.__drop = null;
    }
    drag();
    ok(G.state.allocation.q1.xy[0].port === 6 && G.state.spec.lines[di].channel &&
       G.state.spec.lines[di].channel.out_port === 6, 'U5: the drag moved q1 drive to port 6');
    undo(win);
    ok(G.state.allocation.q1.xy[0].port === 2, 'U5: Ctrl+Z puts q1 drive back on port 2 (got ' + G.state.allocation.q1.xy[0].port + ')');
    ok(JSON.stringify(G.state.spec.lines[di].channel || null) === chBefore,
      'U5: ...and its spec pin (' + JSON.stringify(G.state.spec.lines[di].channel) + ' vs ' + chBefore + ')');
    ok(/Undid: wiring drag/.test(toasts.slice(-1)[0] || ''), 'U5: toast names the drag (got ' + JSON.stringify(toasts.slice(-1)) + ')');
    // two drags walk back LIFO (the restore is in place, so the older drag's
    // allocation-identity guard still holds after the newer one is undone)
    drag();
    host.innerHTML =
      '<div class="iw-port" data-con="1" data-slot="1" data-port="6" data-io="output">' +
      '<span class="iw-port-circle" data-element="q1" data-role="xy"></span></div>' +
      '<div class="iw-port" id="tgt" data-con="1" data-slot="1" data-port="7" data-io="output"></div>';
    G._test.attachWiringDrag();
    drag();
    ok(G.state.allocation.q1.xy[0].port === 7, 'U5: second drag moved q1 drive to port 7');
    undo(win);
    const afterOne = G.state.allocation.q1.xy[0].port;
    undo(win);
    ok(afterOne === 6 && G.state.allocation.q1.xy[0].port === 2,
      'U5: Ctrl+Z twice walks 7 -> 6 -> 2 (got ' + afterOne + ' -> ' + G.state.allocation.q1.xy[0].port + ')');
    // a re-allocation after the drag makes the drag snapshot stale
    host.innerHTML =
      '<div class="iw-port" data-con="1" data-slot="1" data-port="2" data-io="output">' +
      '<span class="iw-port-circle" data-element="q1" data-role="xy"></span></div>' +
      '<div class="iw-port" id="tgt" data-con="1" data-slot="1" data-port="6" data-io="output"></div>';
    G._test.attachWiringDrag();
    drag();
    win.document.getElementById('gen-allocate-btn').dispatchEvent(new win.Event('click', { bubbles: true }));
    await settle();
    const fresh = G.state.allocation;
    const nToasts = toasts.length;
    undo(win);
    ok(G.state.allocation === fresh && !toasts.slice(nToasts).some(t => /wiring drag/.test(t)),
      'U5: after a re-allocation the old drag snapshot is not replayed (got ' + JSON.stringify(toasts.slice(nToasts)) + ')');
  }

  if (fails) {
    console.error('generate_wizundo_struct_selfcheck: ' + fails + ' FAILURES');
    process.exit(1);
  }
  console.log('generate_wizundo_struct_selfcheck: all checks passed (' + asserts + ' assertions)');
})().catch(function (e) {
  console.error('generate_wizundo_struct_selfcheck: crashed — ' + (e && e.stack || e));
  process.exit(1);
});
