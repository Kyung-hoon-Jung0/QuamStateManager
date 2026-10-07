/* docs/301 F35 -- the Populate step's live waveform preview never covers the
 * row being edited or the table header.
 *
 * The panel was sticky at the TOP of the pane, above the populate tables:
 * focusing a cell further down opened it over the header and the rows above
 * the focused one -- the very rows a user compares against.
 *
 *  D1  the panel sits after every populate table and docks at the BOTTOM of
 *      the pane (sticky bottom, never sticky top)
 *  D2  a focused cell the docked panel would cover is scrolled up above it
 *  D3  a cell already clear of the panel is left where it is
 *
 * Run: node tests/generate_preview_dock_selfcheck.cjs   (needs jsdom)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) { console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1); });

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');
const HTML = read('quam_state_manager/web/templates/_generate.html');
const GEN = read('quam_state_manager/web/static/generate.js');
const PREV = read('quam_state_manager/web/static/generate_preview.js');
const CSS = read('quam_state_manager/web/static/style.css');

let fails = 0;
let n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="table-pane">' + HTML + '</div></body>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () {
    return win.Promise.resolve({ json: function () {
      return win.Promise.resolve({ ok: true, error: null, param_errors: {}, plot: { ok: true, traces: [] } });
    } });
  };
  win.PulsesPage = { renderPulsePlot: function () {} };
  new win.Function(GEN).call(win);
  new win.Function(PREV).call(win);
  return win;
}

function setInput(win, id, value) {
  const el = win.document.getElementById(id);
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

function chipTo6(win) {
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, 'gen-chassis-count', '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{ slot: 1, fem: 'mw' }, { slot: 5, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, 'gen-qubit-count', '3');
  const a = win.document.getElementById('gen-chip-arch');
  a.value = 'flux_tunable_coupler'; a.dispatchEvent(new win.Event('change', { bubbles: true }));
  G.goToStep(6);
  return G;
}

function rect(top, bottom) {
  return { top: top, bottom: bottom, left: 0, right: 800, height: bottom - top, width: 800, x: 0, y: top };
}

(async function main() {
  // -- D1: markup + CSS
  {
    const win = makeWorld();
    const doc = win.document;
    const panel = doc.getElementById('gen-pop-preview');
    const step6 = panel && panel.closest('.gen-panel');
    ok(step6 && step6.getAttribute('data-step') === '6', 'D1: the panel lives on the Populate step');
    const hosts = step6 ? step6.querySelectorAll('.gen-pop-host') : [];
    ok(hosts.length >= 5, 'D1: precondition -- the populate table hosts are found (' + hosts.length + ')');
    const allBefore = Array.prototype.every.call(hosts, function (h) {
      return !!(h.compareDocumentPosition(panel) & win.Node.DOCUMENT_POSITION_FOLLOWING);
    });
    ok(allBefore, 'D1: the panel comes after every populate table');
    const m = /\n\.gen-pop-preview\s*\{([^}]*)\}/.exec(CSS);
    const rule = m ? m[1] : '';
    ok(/position:\s*sticky/.test(rule) && /(^|[\s;])bottom:\s*0(?![.\d])/.test(rule),
      'D1: it docks at the bottom of the pane -- rule: ' + rule.trim());
    ok(!/(^|[\s;])top:\s*0(?![.\d])/.test(rule),'D1: and is never sticky at the top (over the header)');
  }

  // -- D2 / D3: the focused cell stays clear of the docked panel
  {
    const win = makeWorld();
    const G = chipTo6(win);
    G.state.spec.populate.resonator = { q1: { readout_length: 1000, readout_amplitude: 0.1 } };
    const doc = win.document;
    const panel = doc.getElementById('gen-pop-preview');
    const tp = doc.getElementById('table-pane');
    const cell = doc.querySelector('.gen-pop-in[data-group="resonator"][data-rid="q1"][data-field="readout_amplitude"]')
              || doc.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    ok(!!cell, 'precondition: a previewable populate cell rendered');
    if (cell) {
      panel.getBoundingClientRect = () => rect(500, 800);
      cell.getBoundingClientRect = () => rect(520, 545);
      tp.scrollTop = 1000;
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(260);
      ok(!panel.hidden, 'D2: precondition -- the panel is shown');
      ok(tp.scrollTop >= 1000 + 45, 'D2: the covered cell is scrolled up above the panel -- scrollTop ' + tp.scrollTop);

      panel.hidden = true;
      cell.getBoundingClientRect = () => rect(120, 145);
      tp.scrollTop = 1000;
      cell.dispatchEvent(new win.Event('focusin', { bubbles: true }));
      await sleep(260);
      ok(!panel.hidden && tp.scrollTop === 1000, 'D3: a cell clear of the panel is left alone -- scrollTop ' + tp.scrollTop);
    }
  }

  if (fails) { console.error(fails + ' of ' + n + ' check(s) failed'); process.exit(1); }
  console.log('generate_preview_dock_selfcheck: all ' + n + ' checks passed');
  process.exit(0);
})();
