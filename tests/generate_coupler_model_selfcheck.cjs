/* Physical model fields, units, refusal, draft and review, using the shipped wizard. */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

// The field list is the Python one (core COUPLER_MODEL_FIELDS); the pytest
// driver tests/test_generate_coupler_model.py passes it in. Run through it.
if (!process.env.COUPLER_MODEL_FIELDS) {
  console.error('COUPLER_MODEL_FIELDS is not set: run this through ' +
                'pytest tests/test_generate_coupler_model.py::test_populate_coupler_model_selfcheck');
  process.exit(1);
}

process.on('uncaughtException', function (e) {
  console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1);
});

const ROOT = path.join(__dirname, '..');
let HTML = fs.readFileSync(
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
  win.NumberInput = { fit() {}, attach() {}, format() {}, strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); } };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.eval(fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/static/coupler-units.js'), 'utf8'));
  new win.Function(GEN_JS).call(win);
  return win;
}

function setInput(win, id, value) {
  const el = win.document.getElementById(id);
  if (!el) throw new Error('no #' + id);
  el.value = String(value);
  el.dispatchEvent(new win.Event('input', { bubbles: true }));
  el.dispatchEvent(new win.Event('change', { bubbles: true }));
}

HTML = HTML.replace(/(<script[^>]*id="gen-coupler-model-fields"[^>]*>)[\s\S]*?(<\/script>)/,
  '$1' + process.env.COUPLER_MODEL_FIELDS + '$2');
function setup(arch) {
  const win = makeWorld(), G = win.QuamGen;
  G.init(); G.goToStep(3); setInput(win, 'gen-chassis-count', 1);
  G.state.spec.instruments.controllers[0].con = 1;
  G.state.spec.instruments.controllers[0].fems = [{slot: 1, fem: 'mw'}, {slot: 2, fem: 'lf'}];
  G.goToStep(4); setInput(win, 'gen-qubit-count', 2);
  const el = win.document.getElementById('gen-chip-arch');
  el.value = arch; el.dispatchEvent(new win.Event('change', {bubbles: true}));
  G.goToStep(6); return win;
}
function input(win, field, value) {
  const el = win.document.querySelector('#gen-pop-coupler-models input[data-field="' + field + '"]');
  if (!el) throw Error('Missing model input ' + field);
  el.value = value; el.dispatchEvent(new win.Event('input', {bubbles: true}));
}
for (const arch of ['fixed_frequency', 'flux_tunable_fixed_coupler']) {
  const w = setup(arch);
  ok(w.document.querySelectorAll('#gen-pop-coupler-models input').length === 0, arch + ': no coupler model fields');
  w.close();
}
for (const arch of ['flux_tunable_coupler', 'fixed_tunable_coupler']) {
  const w = setup(arch), G = w.QuamGen;
  const host = w.document.getElementById('gen-pop-coupler-models');
  ok(host.querySelectorAll('input').length === 9, arch + ': eight physical fields and source');
  ok(!G.state.spec.populate.coupler_models || Object.keys(G.state.spec.populate.coupler_models).length === 0,
     'blank model is absent');
  input(w, 'fc_max_hz', '8');
  ok(host.textContent.includes('Missing: Coupler charging energy'), 'partial model names missing fields immediately');
  const error = host.querySelector('.ui-coupler-model-error');
  ok(error.previousElementSibling === host.querySelector('fieldset > p.muted') &&
     error.nextElementSibling === host.querySelector('.ui-coupler-model-grid'),
     arch + ': inline error sits directly after hint and before field grid');
  ok(host.querySelector('input').getAttribute('aria-invalid') === 'true', 'partial model inline invalid');
  const values = {fc_max_hz: '8', ec_c_hz: '250', d_c: '.15', phi0_v: '.8',
    phi_idle: '.1', g_c_hz: '80', g_t_hz: '90', g_ct_hz: '3'};
  for (const [key, value] of Object.entries(values)) input(w, key, value);
  const id = Object.keys(G.state.spec.populate.coupler_models)[0];
  const stored = G.state.spec.populate.coupler_models[id];
  ok(stored.fc_max_hz === 8e9 && stored.ec_c_hz === 250e6 && stored.g_c_hz === 80e6 &&
     stored.g_t_hz === 90e6 && stored.g_ct_hz === 3e6 && stored.phi0_v === .8 && stored.phi_idle === .1,
     'user units convert to Hz and V');
  ok(stored.source === 'entered in the wizard' && !stored.created_at, 'source default, timestamp deferred until build');
  ok(host.querySelector('.ui-coupler-model-error').hidden, 'complete model clears inline error');
  const draft = JSON.parse(w.sessionStorage.getItem('quam_generate_draft'));
  ok(draft.spec.populate.coupler_models[id].fc_max_hz === 8e9, 'draft stores converted model');
  delete G.state.spec.populate.coupler_models;
  G._test.applyDraft(draft);
  ok(G.state.spec.populate.coupler_models[id].g_t_hz === 90e6, 'draft restores model');
  G._test.renderPopulateTables();
  ok(Number(host.querySelector('[data-field="fc_max_hz"]').value) === 8, 'model survives Populate re-entry in GHz');
  // Decimal sweeps exercise both shipped forms, including 1.07 GHz.
  const fields = JSON.parse(process.env.COUPLER_MODEL_FIELDS);
  w.eval(fs.readFileSync(path.join(ROOT, 'quam_state_manager/web/static/cz-plan.js'), 'utf8'));
  let staged;
  w.fetch = (url, options) => {
    if (url === '/field/edit-batch') staged = JSON.parse(options.body).updates[0].value;
    return new w.Promise(() => {});
  };
  const form = w.document.createElement('form');
  form.dataset.pair = 'q1-2';
  form.innerHTML = fields.map(f => '<input name="' + f.key + '" data-scale="' + f.scale + '">').join('') +
    '<input name="source" value="sweep"><output></output>';
  for (const digits of [2, 3]) {
    for (let n = 100; n <= 130; n++) {
      const raw = (n / 10 ** digits).toFixed(digits);
      for (const f of fields) {
        const value = f.key === 'd_c' ? '.' + String(n) : raw;
        const expected = f.scale > 1 ? n * (f.scale / 10 ** digits) : Number(value);
        input(w, f.key, value);
        ok(G.state.spec.populate.coupler_models[id][f.key] === expected, 'exact wizard ' + f.key + '=' + value);
        G._test.renderPopulateTables();
        const shown = host.querySelector('[data-field="' + f.key + '"]').value;
        input(w, f.key, shown);
        ok(G.state.spec.populate.coupler_models[id][f.key] === expected, 'wizard round trip ' + f.key + '=' + value);
        form.elements.namedItem(f.key).value = shown;
      }
      w.CzStageCoupler(form);
      for (const f of fields) {
        const value = f.key === 'd_c' ? '.' + String(n) : raw;
        const expected = f.scale > 1 ? n * (f.scale / 10 ** digits) : Number(value);
        ok(staged[f.key] === expected, 'exact CZ ' + f.key + '=' + raw);
      }
    }
  }
  for (const [key, raw, expected] of [
    ['fc_max_hz', '1.0700000006', 1070000001],
    ['phi0_v', '0.123456789012345', .123456789012],
    ['phi_idle', '-0.123456789012345', -.123456789012]
  ]) {
    input(w, key, raw);
    ok(G.state.spec.populate.coupler_models[id][key] === expected, 'wizard resolution ' + key);
    G._test.renderPopulateTables();
    const shown = host.querySelector('[data-field="' + key + '"]').value;
    input(w, key, shown);
    ok(G.state.spec.populate.coupler_models[id][key] === expected, 'resolution round trip ' + key);
    form.elements.namedItem(key).value = raw;
    w.CzStageCoupler(form);
    ok(staged[key] === expected, 'CZ resolution ' + key);
    form.elements.namedItem(key).value = shown;
    w.CzStageCoupler(form);
    ok(staged[key] === expected, 'CZ resolution round trip ' + key);
  }
  // Restoring the model display and dispatching unchanged input preserves provenance.
  for (const [key, value] of Object.entries(values)) input(w, key, value);
  G.state.spec.populate.coupler_models[id].created_at = '2026-01-01T00:00:00Z';
  G._test.renderPopulateTables();
  input(w, 'fc_max_hz', '8.000');
  ok(G.state.spec.populate.coupler_models[id].created_at === '2026-01-01T00:00:00Z', 'unchanged input keeps timestamp');
  input(w, 'source', 'revised source');
  ok(!G.state.spec.populate.coupler_models[id].created_at, 'source-only edit clears timestamp for build');
  G.state.spec.populate.coupler_models[id].created_at = 'old';
  input(w, 'g_c_hz', '81');
  ok(!G.state.spec.populate.coupler_models[id].created_at, 'physical edit clears timestamp for build');
  for (const [key, value] of Object.entries(values)) input(w, key, value);
  input(w, 'source', 'entered in the wizard');
  const pair = G.state.spec.qubit_pairs[0];
  G._test.flipPairOrder(pair);
  const flipped = G.state.spec.populate.coupler_models[pair.join('-')];
  ok(flipped.g_c_hz === 90e6 && flipped.g_t_hz === 80e6, 'couplings follow physical qubits on reversal');
  G._test.flipPairOrder(pair);
  ok(G.state.spec.populate.coupler_models[id].g_c_hz === 80e6, 'reversing back restores model');
  G.state.mode = 'regenerate';
  G.state.regenBaselinePopulate = JSON.parse(JSON.stringify(G.state.spec.populate));
  G.state.regenTouched = {'coupler_models|q1-q2|g_c_hz': 1};
  ok(G._test.renameQubit('q1', 'q3') === null, 'rename accepted');
  ok(G.state.spec.populate.coupler_models['q3-q2'].g_c_hz === 80e6, 'model follows renamed qubit');
  ok(G.state.regenBaselinePopulate.coupler_models['q3-q2'].g_c_hz === 80e6 &&
     G.state.regenTouched['coupler_models|q3-q2|g_c_hz'], 'baseline and protection follow rename');
  G._test.renameQubit('q3', 'q1'); G.state.mode = 'generate';
  G._test.renderPopulateTables();
  input(w, 'd_c', '1');
  ok(host.textContent.includes('Invalid: Junction asymmetry'), 'asymmetry upper bound excluded');
  input(w, 'd_c', '.15');
  G.goToStep(8);
  ok(w.document.getElementById('gen-review').textContent.includes('coupler model: set'), 'Review reports set');
  G.goToStep(6);
  for (const key of Object.keys(values)) input(w, key, '');
  ok(Object.keys(G.state.spec.populate.coupler_models).length === 0, 'clearing numeric fields omits model');
  G.goToStep(8);
  ok(w.document.getElementById('gen-review').textContent.includes('coupler model: not set'), 'Review reports not set');
  G.state.spec.populate.coupler_models[id] = {};
  G.goToStep(8);
  ok(w.document.getElementById('gen-review').textContent.includes('coupler model: not set'), 'explicit empty model is not set');
  G._test.prunePopulate({q2: true});
  ok(!G.state.spec.populate.coupler_models[id], 'removed qubit drops its model');
  w.close();
}
if (fails) process.exit(1);
console.log('all checks passed');
