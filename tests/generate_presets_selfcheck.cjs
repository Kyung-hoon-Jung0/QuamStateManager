// Behavioral check for the Populate-step default-value presets
// (generate.js capturePresetSections / applyPreset + the preset bar).
//
// Customer requirement: a "memory archiving section" for default sets —
// save x180/readout/flux/pair defaults once, re-apply them to any chip.
// Pins the capture rule (uniform column → defaults, differing rows →
// overrides, LO/grid never captured), the apply rule (only-empty vs
// overwrite, unmatched-row skip, CR fields dropped on a CZ chip, hidden
// sections skipped), and the post-apply refresh wiring.
//
// Run: node tests/generate_presets_selfcheck.cjs   (needs jsdom)
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
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

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
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  return win;
}

function setInput(win, el, value) {
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

function buildWizard(win, opts) {
  opts = opts || {};
  const G = win.QuamGen;
  G.init();
  G.goToStep(3);
  setInput(win, win.document.getElementById('gen-chassis-count'), '1');
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems =
    opts.mwOnly ? [{ slot: 1, fem: 'mw' }] : [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }];
  G.goToStep(4);
  setInput(win, win.document.getElementById('gen-qubit-count'), '3');
  if (opts.arch) {
    const arch = win.document.getElementById('gen-chip-arch');
    arch.value = opts.arch;
    arch.dispatchEvent(new win.Event('change', { bubbles: true }));
  }
  G.goToStep(6);
  G.QT = win.QuamGen._test;
  return G;
}

// P1: capture — uniform column → defaults; differing rows → overrides;
// LO_frequency / grid_location never captured.
(function captureRules() {
  const win = makeWorld();
  const G = buildWizard(win);
  G.state.spec.populate.pulses = {
    q1: { x180_length: 4e-8, x180_amplitude: 0.1, drag_alpha: 0.5 },
    q2: { x180_length: 4e-8, x180_amplitude: 0.1, drag_alpha: 0.7 },
    q3: { x180_length: 4e-8 }
  };
  G.state.spec.populate.qubit = {
    q1: { RF_freq: 5e9, LO_frequency: 5.1e9, grid_location: '0,0' }
  };
  const s = G.QT.capturePresetSections(['pulses', 'qubit']);
  ok(s.pulses.defaults.x180_length === 4e-8, 'P1: uniform column → defaults');
  ok(s.pulses.defaults.x180_amplitude === 0.1,
    'P1: uniform-across-valued-rows → defaults (q3 has no amp)');
  ok(!('drag_alpha' in s.pulses.defaults), 'P1: differing column not a default');
  ok(s.pulses.overrides.q1.drag_alpha === 0.5 && s.pulses.overrides.q2.drag_alpha === 0.7,
    'P1: differing rows → overrides');
  ok(s.qubit.defaults.RF_freq === 5e9, 'P1: qubit RF captured');
  ok(!('LO_frequency' in s.qubit.defaults), 'P1: LO never captured');
  ok(!('grid_location' in s.qubit.defaults) &&
     !(s.qubit.overrides.q1 && 'grid_location' in s.qubit.overrides.q1),
    'P1: grid_location never captured');
})();

// P2: apply — only-empty vs overwrite; unmatched rows skipped with a note.
(function applyRules() {
  const win = makeWorld();
  const G = buildWizard(win);
  G.state.spec.populate.pulses = { q1: { x180_amplitude: 0.25 } };
  const preset = { sections: { pulses: {
    defaults: { x180_amplitude: 0.1, x180_length: 4e-8 },
    overrides: { q2: { drag_alpha: 0.9 }, q9: { drag_alpha: 0.1 } }
  } } };

  let rep = G.QT.applyPreset(preset, false);   // only-empty
  const pl = G.state.spec.populate.pulses;
  ok(pl.q1.x180_amplitude === 0.25, 'P2: only-empty keeps the existing value');
  ok(pl.q1.x180_length === 4e-8 && pl.q3.x180_amplitude === 0.1,
    'P2: defaults fill every empty cell');
  ok(pl.q2.drag_alpha === 0.9, 'P2: matching override lands');
  ok(rep.skippedRows.length === 1 && rep.skippedRows[0] === 'q9',
    'P2: unmatched override row skipped + reported');

  rep = G.QT.applyPreset(preset, true);        // overwrite
  ok(G.state.spec.populate.pulses.q1.x180_amplitude === 0.1,
    'P2: overwrite replaces the existing value');
})();

// P3: CR pair fields drop cleanly on a CZ chip; flux section skipped on an
// MW-only chip (hidden-section gating).
(function gateFiltering() {
  const win = makeWorld();
  const G = buildWizard(win);          // CZ-tunable (default arch, has LF)
  const preset = { sections: {
    pairs: { defaults: { cr_drive_amplitude: 1.0, cz_amplitude: 0.12 }, overrides: {} },
    flux: { defaults: { joint_offset: 0.05 }, overrides: {} }
  } };
  const rep = G.QT.applyPreset(preset, false);
  const pairId = G.state.spec.qubit_pairs[0].join('-');
  ok(G.state.spec.populate.pairs[pairId].cz_amplitude === 0.12,
    'P3: CZ field applied to the pair rows');
  ok(!('cr_drive_amplitude' in G.state.spec.populate.pairs[pairId]),
    'P3: CR field dropped on a CZ chip');
  ok(rep.droppedFields.indexOf('cr_drive_amplitude') >= 0,
    'P3: dropped field reported');
  ok(G.state.spec.populate.flux.q1.joint_offset === 0.05,
    'P3: flux applied (LF-FEM present)');

  // MW-only chip: the flux section is hidden → skipped entirely.
  const win2 = makeWorld();
  const G2 = buildWizard(win2, { mwOnly: true, arch: 'fixed_frequency' });
  const rep2 = G2.QT.applyPreset(preset, false);
  ok(!G2.state.spec.populate.flux || !G2.state.spec.populate.flux.q1,
    'P3: flux not applied on an MW-only chip');
  ok(rep2.hiddenSections.indexOf('flux') >= 0, 'P3: hidden section reported');
})();

// P4: the preset bar binds on step-6 entry; skipped values never write LO.
(function domSmoke() {
  const win = makeWorld();
  const G = buildWizard(win);
  const bar = win.document.getElementById('gen-preset-bar');
  ok(!!bar && bar.dataset.bound === '1', 'P4: preset bar bound on step entry');
  const sel = win.document.getElementById('gen-preset-select');
  ok(!!sel && sel.options.length >= 1, 'P4: select rendered');
  // Applying a preset that (illegally) carries LO_frequency must not write it.
  const rep = G.QT.applyPreset({ sections: { qubit: {
    defaults: { LO_frequency: 5.1e9, RF_freq: 5.0e9 }, overrides: {} } } }, true);
  ok(G.state.spec.populate.qubit.q1.RF_freq === 5.0e9, 'P4: legal field applied');
  ok(!('LO_frequency' in G.state.spec.populate.qubit.q1),
    'P4: LO_frequency never written by a preset');
  ok(rep.droppedFields.indexOf('LO_frequency') >= 0, 'P4: LO drop reported');
})();

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('generate_presets_selfcheck: all checks passed');
